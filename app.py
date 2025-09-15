import datetime
import os
import matplotlib
matplotlib.use('Agg') # Usar backend 'Agg' para Matplotlib en entornos sin GUI (como servidores Flask)
import matplotlib.pyplot as plt
import numpy as np # Necesario para np.linspace y np.arange
from flask import Flask, request, jsonify, render_template, send_file
from flask_cors import CORS
from io import BytesIO
import uuid # Para nombres de archivo únicos si es necesario
from twilio.rest import Client # Importar Twilio Client

# --- Importaciones para PDF con ReportLab ---
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
import re

# --- Clases de Lógica de Negocio ---
class Estacion:
    """
    Representa una estación de trabajo en la línea de producción.
    Almacena información sobre su tiempo de proceso y relaciones de precedencia.
    También guarda los resultados del cálculo CPM (ES, EF, LS, LF, Holgura).
    """
    def __init__(self, nombre, tiempo, predecesora_nombre=""):
        self.nombre = nombre
        # Validar que el tiempo sea un número y positivo
        if not isinstance(tiempo, (int, float)) or tiempo <= 0:
            raise ValueError(f"El tiempo para la estación '{nombre}' debe ser un número positivo. Recibido: {tiempo}")
        self.tiempo = float(tiempo) # Asegurar que sea float para cálculos precisos
        self.predecesora_nombre = predecesora_nombre
        self.es = 0.0  # Earliest Start
        self.ef = 0.0  # Earliest Finish
        self.ls = 0.0  # Latest Start
        self.lf = 0.0  # Latest Finish
        self.holgura = 0.0
        self.es_critica = False

    def __repr__(self):
        return (f"Estacion(Nombre: {self.nombre}, Tiempo: {self.tiempo:.2f}, Pred: '{self.predecesora_nombre}', "
                f"ES: {self.es:.2f}, EF: {self.ef:.2f}, LS: {self.ls:.2f}, LF: {self.lf:.2f}, Holgura: {self.holgura:.2f}, "
                f"Crítica: {self.es_critica})")

class LineaProduccion:
    """
    Gestiona el conjunto de estaciones, los parámetros de producción y realiza los cálculos
    de CPM, métricas de eficiencia, asignación de empleados y generación de análisis.
    """
    def __init__(self, estaciones_data, unidades_a_producir, num_empleados_disponibles):
        self.estaciones_dict = {} # Diccionario para acceso rápido a estaciones por nombre
        self.estaciones_lista = [] # Lista ordenada de estaciones (según entrada, útil para algunas iteraciones)
        self._procesar_estaciones_data(estaciones_data) # Valida y crea objetos Estacion
        
        if not isinstance(unidades_a_producir, int) or unidades_a_producir < 0:
            raise ValueError("Las unidades a producir deben ser un número entero no negativo.")
        self.unidades_a_producir = unidades_a_producir
        
        if not isinstance(num_empleados_disponibles, int) or num_empleados_disponibles < 0:
            raise ValueError("El número de empleados disponibles debe ser un número entero no negativo.")
        self.num_empleados_disponibles = num_empleados_disponibles
        
        # Resultados de los cálculos
        self.tiempo_total_camino_critico = 0.0
        self.camino_critico_nombres = []
        self.tiempos_acumulados_por_estacion = {} # Para análisis de cuello de botella
        
        self.tiempo_ciclo_calculado = 0.0
        self.tiempo_produccion_total_estimado = 0.0
        self.eficiencia_linea = 0.0
        self.efectividad_linea = 0.0 
        self.cuello_botella_info = {"nombre": "", "tiempo_acumulado": 0.0, "tipo": ""}
        self.empleados_asignados_por_estacion = []

    def _procesar_estaciones_data(self, estaciones_data):
        if not estaciones_data:
            raise ValueError("No se proporcionaron datos de estaciones. La lista de estaciones está vacía.")
        
        self.estaciones_dict.clear()
        self.estaciones_lista.clear()
        nombres_vistos = set()

        for i, data in enumerate(estaciones_data):
            nombre = data.get("nombre")
            tiempo = data.get("tiempo") 
            predecesora = data.get("predecesora", "") # Espera un string único

            if not nombre: 
                 raise ValueError(f"La estación #{i+1} no tiene nombre.")
            if nombre.lower() in nombres_vistos: 
                raise ValueError(f"Nombre de estación duplicado: '{nombre}'. Los nombres deben ser únicos.")
            nombres_vistos.add(nombre.lower())

            try:
                est = Estacion(nombre, tiempo, predecesora)
            except ValueError as e: 
                raise ValueError(f"Error en estación '{nombre}': {e}")

            self.estaciones_dict[est.nombre] = est
            self.estaciones_lista.append(est)

        for est in self.estaciones_lista:
            if est.predecesora_nombre and est.predecesora_nombre not in self.estaciones_dict:
                raise ValueError(f"La predecesora '{est.predecesora_nombre}' para la estación '{est.nombre}' no existe o no coincide exactamente con un nombre de estación definido.")

    def calcular_cpm(self):
        if not self.estaciones_lista: return

        for est in self.estaciones_lista:
            est.es = 0.0
            est.ef = est.tiempo 

        num_estaciones = len(self.estaciones_lista)
        for _ in range(num_estaciones): 
            cambio_realizado = False
            for est in self.estaciones_lista:
                nuevo_es = 0.0
                if est.predecesora_nombre: # Solo una predecesora directa
                    pred_obj = self.estaciones_dict.get(est.predecesora_nombre)
                    if pred_obj: 
                        nuevo_es = pred_obj.ef
                
                if nuevo_es > est.es: 
                    est.es = nuevo_es
                    cambio_realizado = True
                
                nuevo_ef = est.es + est.tiempo
                if nuevo_ef != est.ef: 
                    est.ef = nuevo_ef
            if not cambio_realizado and _ > 0: 
                break
        
        self.tiempo_total_camino_critico = max((est.ef for est in self.estaciones_lista), default=0.0)

        for est in self.estaciones_lista:
            est.lf = self.tiempo_total_camino_critico
            est.ls = est.lf - est.tiempo

        estaciones_inversas_para_ls_lf = self.estaciones_lista[::-1] 
        for _ in range(num_estaciones): 
            cambio_realizado_atras = False
            for est in estaciones_inversas_para_ls_lf:
                sucesores = [s for s in self.estaciones_lista if s.predecesora_nombre == est.nombre]
                nuevo_lf = self.tiempo_total_camino_critico 
                if sucesores:
                    nuevo_lf = min((s.ls for s in sucesores), default=self.tiempo_total_camino_critico)

                if nuevo_lf < est.lf: 
                    est.lf = nuevo_lf
                    cambio_realizado_atras = True
                
                nuevo_ls = est.lf - est.tiempo
                if nuevo_ls != est.ls: 
                    est.ls = nuevo_ls
            if not cambio_realizado_atras and _ > 0:
                break
        
        self.camino_critico_nombres = []
        epsilon = 1e-6 
        for est in self.estaciones_lista:
            est.holgura = est.ls - est.es
            if abs(est.holgura) < epsilon: 
                est.es_critica = True
                self.camino_critico_nombres.append(est.nombre)
            else:
                est.es_critica = False
        
        if self.estaciones_lista:
            estacion_cuello_botella_individual = max(self.estaciones_lista, key=lambda e: e.tiempo)
            self.cuello_botella_info = {
                "nombre": estacion_cuello_botella_individual.nombre,
                "tiempo_proceso_individual": estacion_cuello_botella_individual.tiempo,
                "tipo": "Estación con mayor tiempo de proceso individual"
            }
        else:
            self.cuello_botella_info = {"nombre": "N/A", "tiempo_proceso_individual": 0, "tipo": "N/A"}

    def calcular_metricas_produccion(self):
        if not self.estaciones_lista: 
            self.tiempo_ciclo_calculado = 0.0
            self.tiempo_produccion_total_estimado = 0.0
            self.eficiencia_linea = 0.0
            self.efectividad_linea = 0.0
            return

        if self.unidades_a_producir > 0 and self.tiempo_total_camino_critico > 0:
            self.tiempo_ciclo_calculado = self.tiempo_total_camino_critico / self.unidades_a_producir
        elif self.cuello_botella_info.get("tiempo_proceso_individual", 0) > 0:
            self.tiempo_ciclo_calculado = self.cuello_botella_info["tiempo_proceso_individual"]
        else:
            self.tiempo_ciclo_calculado = 0.0
            
        if self.unidades_a_producir > 0: 
            tiempo_estacion_mas_larga = self.cuello_botella_info.get("tiempo_proceso_individual", 0)
            if tiempo_estacion_mas_larga > 0:
                self.tiempo_produccion_total_estimado = self.tiempo_total_camino_critico + (self.unidades_a_producir - 1) * tiempo_estacion_mas_larga if self.unidades_a_producir > 1 else self.tiempo_total_camino_critico
            else: 
                self.tiempo_produccion_total_estimado = self.tiempo_total_camino_critico 
        else: 
            self.tiempo_produccion_total_estimado = self.tiempo_total_camino_critico

        sum_tiempos_individuales_tareas = sum(est.tiempo for est in self.estaciones_lista)
        
        tiempo_cuello_botella_real = self.cuello_botella_info.get("tiempo_proceso_individual", 0)
        if tiempo_cuello_botella_real == 0 and self.estaciones_lista: 
            tiempo_cuello_botella_real = max((e.tiempo for e in self.estaciones_lista), default=0)

        if self.num_empleados_disponibles > 0 and tiempo_cuello_botella_real > 0:
            denominador_eficiencia = self.num_empleados_disponibles * tiempo_cuello_botella_real
            if denominador_eficiencia > 0:
                self.eficiencia_linea = (sum_tiempos_individuales_tareas / denominador_eficiencia) * 100
            else: 
                self.eficiencia_linea = 0.0 if sum_tiempos_individuales_tareas > 0 else 100.0
        else: 
             self.eficiencia_linea = 0.0 if sum_tiempos_individuales_tareas > 0 else 100.0

        self.eficiencia_linea = min(max(self.eficiencia_linea, 0.0), 100.0)
        self.efectividad_linea = self.eficiencia_linea

    def asignar_empleados(self):
        self.empleados_asignados_por_estacion = []
        if not self.estaciones_lista or self.num_empleados_disponibles == 0:
            for est in self.estaciones_lista:
                self.empleados_asignados_por_estacion.append({"nombre": est.nombre, "empleados": 0})
            return

        total_tiempo_tareas = sum(est.tiempo for est in self.estaciones_lista)
        if total_tiempo_tareas == 0: 
            num_est_activas = len([e for e in self.estaciones_lista if e.tiempo >= 0]) 
            empleados_por_estacion_si_cero_tiempo = self.num_empleados_disponibles // num_est_activas if num_est_activas > 0 else 0
            resto_empleados = self.num_empleados_disponibles % num_est_activas if num_est_activas > 0 else self.num_empleados_disponibles
            
            temp_asignaciones = []
            for i, est in enumerate(self.estaciones_lista):
                asignados = empleados_por_estacion_si_cero_tiempo
                if i < resto_empleados:
                    asignados +=1
                temp_asignaciones.append({"nombre": est.nombre, "empleados": asignados if est.tiempo >=0 else 0})
            self.empleados_asignados_por_estacion = temp_asignaciones
            return

        asignaciones_temp = []
        for est in self.estaciones_lista:
            proporcion = est.tiempo / total_tiempo_tareas if total_tiempo_tareas > 0 else (1.0/len(self.estaciones_lista) if self.estaciones_lista else 0)
            empleados_ideal = proporcion * self.num_empleados_disponibles
            asignaciones_temp.append({
                "nombre": est.nombre, 
                "ideal": empleados_ideal, 
                "asignados_base": int(empleados_ideal), 
                "fraccion": empleados_ideal - int(empleados_ideal) 
            })
        
        empleados_asignados_sum = sum(a["asignados_base"] for a in asignaciones_temp)
        empleados_restantes_por_asignar = self.num_empleados_disponibles - empleados_asignados_sum

        asignaciones_temp.sort(key=lambda x: x["fraccion"], reverse=True)

        for i in range(int(round(empleados_restantes_por_asignar))): 
            if i < len(asignaciones_temp):
                asignaciones_temp[i]["asignados_base"] += 1
        
        map_nombre_a_asignacion = {a["nombre"]: a["asignados_base"] for a in asignaciones_temp}
        self.empleados_asignados_por_estacion = []
        for est_original in self.estaciones_lista: 
            self.empleados_asignados_por_estacion.append({
                "nombre": est_original.nombre,
                "empleados": map_nombre_a_asignacion.get(est_original.nombre, 0) 
            })

        suma_final_asignados = sum(item["empleados"] for item in self.empleados_asignados_por_estacion)
        diferencia_final = self.num_empleados_disponibles - suma_final_asignados
        
        idx_ajuste = 0
        iter_count_safety = 0 
        max_iters_safety = len(self.empleados_asignados_por_estacion) * abs(diferencia_final) + len(self.empleados_asignados_por_estacion) + 1

        if diferencia_final != 0:
            self.empleados_asignados_por_estacion.sort(key=lambda x: x["empleados"], reverse=(diferencia_final < 0))

        while diferencia_final != 0 and self.empleados_asignados_por_estacion and iter_count_safety < max_iters_safety :
            est_idx_ajuste = idx_ajuste % len(self.empleados_asignados_por_estacion)
            if diferencia_final > 0: 
                self.empleados_asignados_por_estacion[est_idx_ajuste]["empleados"] += 1
                diferencia_final -=1
            elif diferencia_final < 0: 
                if self.empleados_asignados_por_estacion[est_idx_ajuste]["empleados"] > 0:
                    self.empleados_asignados_por_estacion[est_idx_ajuste]["empleados"] -= 1
                    diferencia_final +=1
            idx_ajuste += 1
            iter_count_safety += 1
        
        if iter_count_safety >= max_iters_safety and diferencia_final != 0:
            print(f"ADVERTENCIA: El ajuste de asignación de empleados no pudo reconciliar la diferencia de {diferencia_final} empleados después de {iter_count_safety} iteraciones.")
        
        original_order_map = {est.nombre: i for i, est in enumerate(self.estaciones_lista)}
        self.empleados_asignados_por_estacion.sort(key=lambda x: original_order_map.get(x["nombre"], float('inf')))

    def generar_texto_analisis_resultados(self):
        analisis = "ANÁLISIS DE RESULTADOS DEL BALANCEO DE LÍNEA:\n"
        analisis += "="*50 + "\n\n"

        analisis += "**I. MÉTODO DE LA RUTA CRÍTICA (CPM):**\n"
        analisis += f"- **Tiempo Total del Proyecto (Camino Crítico):** {self.tiempo_total_camino_critico:.2f} minutos.\n"
        analisis += f"  Este es el tiempo mínimo para completar todas las tareas si se siguen las precedencias.\n"
        crit_est_str = ', '.join(self.camino_critico_nombres) if self.camino_critico_nombres else 'N/A (Revisar datos si hay estaciones)'
        analisis += f"- **Estaciones en el Camino Crítico:** {crit_est_str}\n"
        analisis += "  Cualquier retraso en estas estaciones impactará directamente la duración total del proyecto.\n"
        
        analisis += "\nDetalle de Estaciones (ES, EF, LS, LF, Holgura):\n"
        for est in self.estaciones_lista:
            critica_tag = " (CRÍTICA)" if est.es_critica else ""
            analisis += (f"  - **{est.nombre}**: T={est.tiempo:.2f}, ES={est.es:.2f}, EF={est.ef:.2f}, "
                         f"LS={est.ls:.2f}, LF={est.lf:.2f}, Holgura={est.holgura:.2f}{critica_tag}\n")

        analisis += "\n**II. MÉTRICAS CLAVE DE PRODUCCIÓN:**\n"
        analisis += f"- **Eficiencia de la Línea:** **{self.eficiencia_linea:.2f}%**.\n"
        if self.eficiencia_linea == 0 and sum(e.tiempo for e in self.estaciones_lista) > 0 :
             analisis += "  Interpretación: La eficiencia es 0%. Esto puede ocurrir si no hay empleados asignados, el tiempo del cuello de botella es cero, o hay problemas en los datos de entrada.\n"
        elif self.eficiencia_linea >= 90:
            analisis += "  Interpretación: Excelente. Indica un muy buen balanceo y aprovechamiento de los recursos (empleados) en relación con el trabajo total y el ritmo del cuello de botella.\n"
        elif self.eficiencia_linea >= 75:
            analisis += "  Interpretación: Bueno. La línea está bien balanceada, pero pueden existir oportunidades de mejora para optimizar el uso de recursos.\n"
        elif self.eficiencia_linea >= 50:
            analisis += "  Interpretación: Moderado. Existen desbalances significativos. Algunas estaciones/empleados podrían estar sobrecargados mientras otros están ociosos.\n"
        else:
            analisis += "  Interpretación: Bajo. Indica un desbalance considerable. Se requiere una revisión profunda para redistribuir tareas o reasignar recursos.\n"

        if self.cuello_botella_info.get("nombre") and self.cuello_botella_info["nombre"] != "N/A":
            cb_nombre = self.cuello_botella_info['nombre']
            cb_tiempo_ind = self.cuello_botella_info.get('tiempo_proceso_individual', 0)
            cb_tipo = self.cuello_botella_info.get('tipo', 'Desconocido')
            analisis += (f"- **Cuello de Botella Identificado:** Estación **'{cb_nombre}'**.\n"
                         f"  - Tipo: {cb_tipo}.\n"
                         f"  - Tiempo de Proceso Individual: {cb_tiempo_ind:.2f} minutos.\n"
                         "  Esta estación, con el mayor tiempo de tarea individual, dicta el ritmo máximo de producción de la línea (Takt Time de la línea).\n")
        else:
            analisis += "- Cuello de Botella: No identificado o no aplicable.\n"
        
        if self.unidades_a_producir > 0:
            analisis += (f"- **Tiempo de Ciclo por Unidad (Promedio Proyecto):** {self.tiempo_ciclo_calculado:.2f} minutos/unidad.\n"
                         f"  Este es el tiempo promedio para producir una unidad considerando la duración total del proyecto para el lote.\n")
            if self.cuello_botella_info.get("tiempo_proceso_individual", 0) > 0:
                 analisis += (f"- **Tiempo de Ciclo de Línea (Takt Time):** {self.cuello_botella_info['tiempo_proceso_individual']:.2f} minutos/unidad.\n"
                              f"  La línea no puede producir más rápido que una unidad cada {self.cuello_botella_info['tiempo_proceso_individual']:.2f} minutos debido a la estación '{self.cuello_botella_info['nombre']}'.\n")
        else:
            analisis += "- Tiempo de Ciclo por Unidad: No aplicable (0 unidades a producir).\n"
        
        analisis += f"- **Tiempo Total de Producción Estimado (para {self.unidades_a_producir} unidades):** {self.tiempo_produccion_total_estimado:.2f} minutos.\n"
        analisis += "  Estimación del tiempo total desde el inicio de la primera unidad hasta la finalización de la última.\n"

        analisis += "\n**III. ASIGNACIÓN DE EMPLEADOS:**\n"
        if self.empleados_asignados_por_estacion:
            for asignacion in self.empleados_asignados_por_estacion:
                analisis += f"  - Estación **'{asignacion['nombre']}'**: {asignacion['empleados']} empleado(s) asignado(s).\n"
            total_asignados = sum(a['empleados'] for a in self.empleados_asignados_por_estacion)
            analisis += f"  Total Empleados Asignados: {total_asignados} (Disponibles: {self.num_empleados_disponibles}).\n"
            if total_asignados != self.num_empleados_disponibles:
                analisis += f"  ADVERTENCIA: El número de empleados asignados ({total_asignados}) no coincide con los disponibles ({self.num_empleados_disponibles}). Revisar lógica de asignación o datos.\n"
        else:
            analisis += "  No se realizó asignación de empleados (0 empleados disponibles o sin estaciones).\n"

        analisis += "\n**IV. RECOMENDACIONES GENERALES:**\n"
        recomendaciones = []
        if self.eficiencia_linea < 75:
            recomendaciones.append("Revisar la distribución de tareas: Mover tareas de estaciones sobrecargadas (especialmente las críticas o cuellos de botella) a estaciones con holgura o menor carga.")
            if self.cuello_botella_info.get("nombre") and self.cuello_botella_info["nombre"] != "N/A":
                 recomendaciones.append(f"Optimizar la estación cuello de botella ('{self.cuello_botella_info['nombre']}'): Aplicar técnicas de mejora de procesos (ej. SMED, 5S, eliminación de desperdicios) para reducir su tiempo de ciclo.")
        
        if any(est.holgura > est.tiempo * 0.5 and est.tiempo > 0 for est in self.estaciones_lista if not est.es_critica):
            recomendaciones.append("Considerar la combinación de tareas en estaciones con mucha holgura o la asignación de tareas adicionales si es posible.")

        if self.num_empleados_disponibles > 0 and len(self.estaciones_lista) > self.num_empleados_disponibles:
             recomendaciones.append("Evaluar la posibilidad de agrupar estaciones si el número de empleados es menor que el número de estaciones y los tiempos de ciclo lo permiten, para mejorar el flujo.")
        
        recomendaciones.append("Capacitación cruzada (Cross-training): Entrenar a los empleados para que puedan operar en múltiples estaciones. Esto aumenta la flexibilidad y ayuda a cubrir cuellos de botella temporales o ausencias.")
        recomendaciones.append("Implementar un sistema de producción Pull (Kanban): Si es aplicable, esto puede ayudar a suavizar el flujo de producción y reducir el trabajo en curso (WIP).")
        recomendaciones.append("Monitoreo continuo: Medir el rendimiento real de la línea y compararlo con estos cálculos. Ajustar según sea necesario.")
        
        if not recomendaciones:
            analisis += "- La línea parece estar razonablemente balanceada según los datos. Continuar monitoreando y buscando mejoras incrementales.\n"
        else:
            for i, rec in enumerate(recomendaciones):
                analisis += f"- {rec}\n"
        
        analisis += "\nNota: Estas son recomendaciones generales. Un análisis detallado in situ es crucial para la optimización efectiva.\n"
        return analisis

# --- Configuración de Flask ---
app = Flask(__name__, template_folder='.', static_folder='static') 
CORS(app) 

TEMP_CHART_DIR = os.path.join(app.static_folder, 'temp_charts')
os.makedirs(TEMP_CHART_DIR, exist_ok=True)

# --- Configuración de Twilio ---
# Credenciales proporcionadas por el usuario
USER_TWILIO_ACCOUNT_SID = "ACe6fc51bff702ab5a8ddd10dd956a5313"
USER_TWILIO_AUTH_TOKEN = "63d61de04e845e01a3ead4d8f941fcdd"
USER_TWILIO_WHATSAPP_FROM_NUMBER = "+14155238886" # Número de Sandbox de Twilio
USER_DESTINATION_WHATSAPP_NUMBER = "+573222074527"

# Usar las credenciales proporcionadas por el usuario directamente
# Si las variables de entorno existen, tendrán precedencia.
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", USER_TWILIO_ACCOUNT_SID)
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", USER_TWILIO_AUTH_TOKEN)
TWILIO_WHATSAPP_FROM_NUMBER = os.environ.get("TWILIO_WHATSAPP_NUMBER", USER_TWILIO_WHATSAPP_FROM_NUMBER) 
DESTINATION_WHATSAPP_NUMBER = os.environ.get("DESTINATION_WHATSAPP_NUMBER", USER_DESTINATION_WHATSAPP_NUMBER)

twilio_client = None
# Verificación de las credenciales
if TWILIO_ACCOUNT_SID and not TWILIO_ACCOUNT_SID.startswith("ACxx") and \
   TWILIO_AUTH_TOKEN and len(TWILIO_AUTH_TOKEN) == 32 and \
   TWILIO_WHATSAPP_FROM_NUMBER and DESTINATION_WHATSAPP_NUMBER:
    print(f"INFO: Intentando inicializar Twilio Client con SID: {TWILIO_ACCOUNT_SID[:5]}... y Token: {'*'*(len(TWILIO_AUTH_TOKEN)-4) + TWILIO_AUTH_TOKEN[-4:]}")
    try:
        twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        print("INFO: Cliente de Twilio inicializado correctamente.")
    except Exception as e:
        print(f"ERROR AL INICIALIZAR TWILIO CLIENT: {e}. Las alertas de WhatsApp no funcionarán.")
        twilio_client = None 
else:
    print("ADVERTENCIA: Credenciales o números de Twilio no configurados correctamente o con formato inválido. Las alertas de WhatsApp no funcionarán.")
    print(f"  TWILIO_ACCOUNT_SID: {'OK' if TWILIO_ACCOUNT_SID and not TWILIO_ACCOUNT_SID.startswith('ACxx') else 'PROBLEMA - Valor: ' + str(TWILIO_ACCOUNT_SID)}")
    print(f"  TWILIO_AUTH_TOKEN: {'OK' if TWILIO_AUTH_TOKEN and len(TWILIO_AUTH_TOKEN) == 32 else 'PROBLEMA - Longitud: ' + str(len(TWILIO_AUTH_TOKEN) if TWILIO_AUTH_TOKEN else 0)}")
    print(f"  TWILIO_WHATSAPP_FROM_NUMBER: {'OK' if TWILIO_WHATSAPP_FROM_NUMBER else 'PROBLEMA - Valor: ' + str(TWILIO_WHATSAPP_FROM_NUMBER)}")
    print(f"  DESTINATION_WHATSAPP_NUMBER: {'OK' if DESTINATION_WHATSAPP_NUMBER else 'PROBLEMA - Valor: ' + str(DESTINATION_WHATSAPP_NUMBER)}")
    twilio_client = None


LOW_EFFICIENCY_THRESHOLD = 60 

def enviar_alerta_balanceo_whatsapp(mensaje):
    if not twilio_client:
        print("INFO ALERTA WHATSAPP: Cliente de Twilio no disponible. Mensaje no enviado.")
        return False
    if not TWILIO_WHATSAPP_FROM_NUMBER or not DESTINATION_WHATSAPP_NUMBER:
        print("INFO ALERTA WHATSAPP: Números de WhatsApp (origen o destino) no configurados. Mensaje no enviado.")
        return False
    
    print(f"INFO ALERTA WHATSAPP: Intentando enviar mensaje desde {TWILIO_WHATSAPP_FROM_NUMBER} hacia {DESTINATION_WHATSAPP_NUMBER}")
    try:
        message_instance = twilio_client.messages.create(
            from_=f'whatsapp:{TWILIO_WHATSAPP_FROM_NUMBER}',
            body=mensaje,
            to=f'whatsapp:{DESTINATION_WHATSAPP_NUMBER}'
        )
        print(f"INFO ALERTA WHATSAPP: Mensaje Twilio SID: {message_instance.sid}, Estado: {message_instance.status}")
        if message_instance.error_code:
            print(f"ERROR ALERTA WHATSAPP (POST-ENVÍO): Código: {message_instance.error_code}, Mensaje: {message_instance.error_message}")
            return False
        return True
    except Exception as e:
        print(f"ERROR CRÍTICO ALERTA WHATSAPP: Excepción al enviar: {e}")
        if hasattr(e, 'status'): print(f"  Twilio Exception Status: {e.status}")
        if hasattr(e, 'code'): print(f"  Twilio Exception Code: {e.code}")
        if hasattr(e, 'message'): print(f"  Twilio Exception Message: {e.message}")
        if hasattr(e, 'more_info'): print(f"  Twilio Exception More Info: {e.more_info}")
        return False

def limpiar_graficos_antiguos(directorio=TEMP_CHART_DIR, max_edad_segundos=3600): 
    ahora = datetime.datetime.now().timestamp()
    try:
        for nombre_archivo in os.listdir(directorio):
            ruta_archivo = os.path.join(directorio, nombre_archivo)
            if os.path.isfile(ruta_archivo):
                try:
                    if ahora - os.path.getmtime(ruta_archivo) > max_edad_segundos:
                        os.remove(ruta_archivo)
                except FileNotFoundError: 
                    pass
    except Exception as e:
        print(f"ERROR: No se pudieron limpiar gráficos antiguos: {e}")

def generar_nombres_archivos_graficos():
    timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')
    uid = uuid.uuid4().hex[:8] 
    pie_name = f"grafico_distribucion_tiempo_{timestamp}_{uid}.png"
    bar_name = f"grafico_asignacion_empleados_{timestamp}_{uid}.png"
    return os.path.join(TEMP_CHART_DIR, pie_name), os.path.join(TEMP_CHART_DIR, bar_name)

def generar_graficos_matplotlib(linea_obj, pie_path, bar_path):
    plt.style.use('seaborn-v0_8-whitegrid') 
    pie_generated = False
    static_pie_path = None
    
    if linea_obj.estaciones_lista and sum(est.tiempo for est in linea_obj.estaciones_lista) > 0:
        nombres_est = [est.nombre for est in linea_obj.estaciones_lista]
        tiempos_est = [est.tiempo for est in linea_obj.estaciones_lista]
        num_pie_segments = len(nombres_est)
        colors_pie = None 
        if num_pie_segments > 0:
            cmap_name_pie = 'viridis' if num_pie_segments > 10 else 'Set2'
            try:
                cmap_pie_obj = plt.colormaps.get_cmap(cmap_name_pie)
                num_colors_needed = num_pie_segments
                if hasattr(cmap_pie_obj, 'N') and num_colors_needed > cmap_pie_obj.N : 
                     colors_pie = [cmap_pie_obj(i % cmap_pie_obj.N) for i in range(num_colors_needed)] 
                else: 
                     colors_pie = [cmap_pie_obj(i) for i in np.linspace(0, 1, num_colors_needed)]
            except Exception as e_cmap_pie:
                print(f"Error obteniendo colormap para pie: {e_cmap_pie}. Usando default.")
        
        try:
            plt.figure(figsize=(8, 8)) 
            wedges, texts, autotexts = plt.pie(
                tiempos_est, 
                labels=None, 
                autopct='%1.1f%%', 
                startangle=140, 
                colors=colors_pie, 
                pctdistance=0.85, 
                wedgeprops={'edgecolor': 'white', 'linewidth': 1.5} 
            )
            plt.setp(autotexts, size=10, weight="bold", color="white") 
            plt.title('Distribución del Tiempo de Tarea por Estación', fontsize=16, pad=20)
            plt.axis('equal')
            if len(nombres_est) > 6:
                 plt.legend(wedges, nombres_est, title="Estaciones", loc="center left", bbox_to_anchor=(1, 0, 0.5, 1), fontsize=9)
            else:
                 plt.legend(wedges, nombres_est, title="Estaciones", loc="best", fontsize=9)
            plt.tight_layout(rect=[0, 0, 0.85 if len(nombres_est) > 6 else 1, 1]) 
            plt.savefig(pie_path)
            plt.close()
            pie_generated = True
        except Exception as e_pie:
            print(f"ERROR generando gráfico de pastel: {e_pie}")
    
    if pie_generated and os.path.exists(pie_path):
        static_pie_path = os.path.join('temp_charts', os.path.basename(pie_path))
    
    bar_generated = False
    static_bar_path = None
    if linea_obj.empleados_asignados_por_estacion and any(item["empleados"] > 0 for item in linea_obj.empleados_asignados_por_estacion):
        n_emp_chart = [item["nombre"] for item in linea_obj.empleados_asignados_por_estacion]
        c_emp_chart = [item["empleados"] for item in linea_obj.empleados_asignados_por_estacion]
        num_bars = len(n_emp_chart)
        bar_colors = None 
        if num_bars > 0:
            try:
                cmap_bar_obj = plt.colormaps.get_cmap('coolwarm')
                bar_colors = [cmap_bar_obj(i) for i in np.linspace(0.2, 0.8, num_bars)] 
            except Exception as e_cmap_bar:
                print(f"Error obteniendo colormap para barras: {e_cmap_bar}. Usando default.")
        try:
            plt.figure(figsize=(max(10, len(n_emp_chart) * 0.7), 7)) 
            bars = plt.bar(n_emp_chart, c_emp_chart, color=bar_colors, edgecolor='black', linewidth=0.7)
            plt.xlabel('Estación de Trabajo', fontsize=13, labelpad=15)
            plt.ylabel('Número de Empleados Asignados', fontsize=13, labelpad=15)
            plt.title('Asignación de Empleados por Estación', fontsize=16, pad=20)
            plt.xticks(rotation=45, ha='right', fontsize=10)
            max_empleados = int(max(c_emp_chart, default=0))
            plt.yticks(fontsize=10, ticks=np.arange(0, max_empleados + 2, 1)) 
            plt.grid(axis='y', linestyle=':', alpha=0.7)
            for bar_item in bars:
                yval = bar_item.get_height()
                if yval > 0: 
                    plt.text(bar_item.get_x() + bar_item.get_width()/2.0, yval + 0.1, int(yval), ha='center', va='bottom', fontsize=9, weight='semibold')
            plt.tight_layout()
            plt.savefig(bar_path)
            plt.close()
            bar_generated = True
        except Exception as e_bar:
            print(f"ERROR generando gráfico de barras: {e_bar}") 
    
    if bar_generated and os.path.exists(bar_path):
         static_bar_path = os.path.join('temp_charts', os.path.basename(bar_path))
            
    return static_pie_path, static_bar_path

@app.route('/')
def serve_index():
    return render_template('index.html')

@app.route('/api/line_balance/calculate', methods=['POST'])
def calculate_line_balance_api():
    limpiar_graficos_antiguos() 
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No se recibieron datos JSON."}), 400

        unidades = data.get('unidades_a_producir')
        empleados = data.get('num_empleados_disponibles')
        estaciones_data = data.get('estaciones_data', [])

        if not isinstance(unidades, int) or unidades < 0:
            return jsonify({"error": "Unidades a producir debe ser un número entero no negativo."}), 400
        if not isinstance(empleados, int) or empleados < 0:
            return jsonify({"error": "Empleados disponibles debe ser un número entero no negativo."}), 400
        if not estaciones_data:
             return jsonify({"error": "No se proporcionaron datos de estaciones."}), 400

        linea = LineaProduccion(estaciones_data, unidades, empleados)
        linea.calcular_cpm()
        linea.calcular_metricas_produccion()
        linea.asignar_empleados()

        pie_file_abs, bar_file_abs = generar_nombres_archivos_graficos()
        static_pie_url, static_bar_url = generar_graficos_matplotlib(linea, pie_file_abs, bar_file_abs)
        
        analisis_texto = linea.generar_texto_analisis_resultados()
        
        # Notificación de optimización completada
        mensaje_optimizacion_completa = (
            f"✅ Optimización de Línea Completada!\n"
            f"Unidades: {unidades}, Empleados: {empleados}\n"
            f"Eficiencia Calculada: {linea.eficiencia_linea:.2f}%\n"
            f"Tiempo Total del Proyecto: {linea.tiempo_total_camino_critico:.2f} min\n"
            f"Estaciones Críticas: {len(linea.camino_critico_nombres)}\n"
            f"Cuello de Botella: '{linea.cuello_botella_info.get('nombre', 'N/A')}' ({linea.cuello_botella_info.get('tiempo_proceso_individual', 0):.2f} min)"
        )
        enviar_alerta_balanceo_whatsapp(mensaje_optimizacion_completa)

        if linea.eficiencia_linea < LOW_EFFICIENCY_THRESHOLD and sum(e.tiempo for e in linea.estaciones_lista) > 0 :
            mensaje_baja_eficiencia = (
                f"⚠️ ALERTA: BAJA EFICIENCIA ({linea.eficiencia_linea:.2f}%) DETECTADA ⚠️\n"
                f"Umbral: <{LOW_EFFICIENCY_THRESHOLD}%. Se recomienda revisión urgente."
            )
            enviar_alerta_balanceo_whatsapp(mensaje_baja_eficiencia)

        estaciones_detalle_cpm = [{
            "nombre": est.nombre, "tiempo": est.tiempo, "predecesora": est.predecesora_nombre,
            "es": est.es, "ef": est.ef, "ls": est.ls, "lf": est.lf, 
            "holgura": est.holgura, "es_critica": est.es_critica
        } for est in linea.estaciones_lista]

        response_data = {
            "mensaje": "Cálculo de balanceo de línea completado exitosamente.",
            "input_data_original": data, 
            "resultados_calculados": { 
                "tiempo_total_camino_critico": linea.tiempo_total_camino_critico,
                "camino_critico_nombres": linea.camino_critico_nombres,
                "estaciones_cpm_detalle": estaciones_detalle_cpm,
                "tiempo_ciclo_calculado": linea.tiempo_ciclo_calculado,
                "tiempo_produccion_total_estimado": linea.tiempo_produccion_total_estimado,
                "eficiencia_linea": linea.eficiencia_linea,
                "efectividad_linea": linea.efectividad_linea,
                "cuello_botella_info": linea.cuello_botella_info,
                "empleados_asignados_por_estacion": linea.empleados_asignados_por_estacion,
                "analisis_texto": analisis_texto,
                "grafico_pie_url": static_pie_url, 
                "grafico_barras_url": static_bar_url,
            }
        }
        return jsonify(response_data), 200

    except ValueError as ve: 
        print(f"ERROR DE VALIDACIÓN en /api/line_balance/calculate: {str(ve)}")
        return jsonify({"error": f"Error en los datos de entrada: {str(ve)}"}), 400
    except Exception as e: 
        import traceback
        print(f"ERROR INTERNO en /api/line_balance/calculate: {traceback.format_exc()}")
        enviar_alerta_balanceo_whatsapp(f"❌ ERROR Crítico en Cálculo de Balanceo: {type(e).__name__} - {str(e)}") 
        return jsonify({"error": f"Error interno del servidor: {str(e)}. Por favor, contacte al administrador."}), 500

def _setup_pdf_styles():
    styles = getSampleStyleSheet()
    if 'MainTitle' not in styles:
        styles.add(ParagraphStyle(name='MainTitle', parent=styles['h1'], fontSize=20, alignment=1, spaceAfter=20, textColor=colors.HexColor("#1A237E")))
    if 'SubTitle' not in styles:
        styles.add(ParagraphStyle(name='SubTitle', parent=styles['h2'], fontSize=16, spaceBefore=12, spaceAfter=8, textColor=colors.HexColor("#0D47A1")))
    if 'SectionTitle' not in styles:
        styles.add(ParagraphStyle(name='SectionTitle', parent=styles['h3'], fontSize=13, spaceBefore=10, spaceAfter=6, textColor=colors.HexColor("#01579B"), fontName='Helvetica-Bold'))
    
    normal_style_base = styles.get('Normal', ParagraphStyle(name='Normal_Fallback', fontSize=10)) 
    if 'Normal' not in styles and 'Normal_Fallback' not in styles: 
        styles.add(normal_style_base)
    elif 'Normal' not in styles and 'Normal_Fallback' in styles: 
        normal_style_base = styles['Normal_Fallback']

    if 'BodyText' not in styles:
        styles.add(ParagraphStyle(name='BodyText', parent=normal_style_base, fontSize=10, leading=14, spaceAfter=6))
    
    if 'BulletPoint' not in styles: 
        bullet_parent = styles.get('Bullet', normal_style_base) 
        styles.add(ParagraphStyle(name='BulletPoint', parent=bullet_parent, fontSize=10, leading=14, spaceBefore=2, leftIndent=20))
    
    body_text_style_base = styles.get('BodyText', normal_style_base)
    if 'BodyText' not in styles and 'BodyText' not in styles: 
         styles.add(ParagraphStyle(name='BodyText_Temp', parent=normal_style_base, fontSize=10, leading=14, spaceAfter=6))
         body_text_style_base = styles['BodyText_Temp']

    if 'BoldText' not in styles:
        styles.add(ParagraphStyle(name='BoldText', parent=body_text_style_base, fontName='Helvetica-Bold')) 
    if 'SmallText' not in styles:
        styles.add(ParagraphStyle(name='SmallText', parent=normal_style_base, fontSize=8, textColor=colors.grey))
    if 'CriticalText' not in styles:
        styles.add(ParagraphStyle(name='CriticalText', parent=body_text_style_base, textColor=colors.red, fontName='Helvetica-Bold'))
    return styles

def _format_paragraph_pdf(text, style_name, styles_obj):
    text = text.replace("\n", "<br/>")
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    style_to_use = styles_obj.get(style_name, styles_obj.get('BodyText', styles_obj['Normal']))
    return Paragraph(text, style_to_use)

@app.route('/api/line_balance/report/pdf', methods=['POST'])
def generate_pdf_report_api():
    try:
        payload = request.get_json() 
        if not payload:
            return jsonify({"error": "No se recibieron datos para generar el reporte."}), 400

        input_data = payload.get('input_data_original', {})
        results = payload.get('resultados_calculados', {})

        if not input_data or not results:
            return jsonify({"error": "Datos para el reporte son incompletos (faltan datos de entrada o resultados)."}), 400

        unidades = input_data.get('unidades_a_producir')
        empleados = input_data.get('num_empleados_disponibles')
        estaciones_data_input = input_data.get('estaciones_data', [])

        tiempo_total_camino_critico = results.get("tiempo_total_camino_critico", 0.0)
        camino_critico_nombres = results.get("camino_critico_nombres", [])
        estaciones_cpm_detalle = results.get("estaciones_cpm_detalle", [])
        eficiencia_linea = results.get("eficiencia_linea", 0.0)
        cuello_botella_info = results.get("cuello_botella_info", {})
        tiempo_ciclo_calculado = results.get("tiempo_ciclo_calculado", 0.0)
        tiempo_produccion_total_estimado = results.get("tiempo_produccion_total_estimado", 0.0)
        empleados_asignados_por_estacion = results.get("empleados_asignados_por_estacion", [])
        analisis_texto_completo = results.get("analisis_texto", "Análisis no disponible.")
        grafico_pie_url_rel = results.get("grafico_pie_url")
        grafico_barras_url_rel = results.get("grafico_barras_url")

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter,
                                rightMargin=0.75*inch, leftMargin=0.75*inch,
                                topMargin=0.75*inch, bottomMargin=0.75*inch)
        
        styles = _setup_pdf_styles() 
        story = []

        story.append(Paragraph("Reporte Detallado de Balanceo de Línea de Producción", styles['MainTitle']))
        story.append(Paragraph(f"Fecha de Generación: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles['SmallText']))
        story.append(Spacer(1, 0.25 * inch))

        story.append(Paragraph("1. Parámetros de Simulación", styles['SubTitle']))
        story.append(_format_paragraph_pdf(f"**Unidades a Producir:** {unidades}", 'BodyText', styles))
        story.append(_format_paragraph_pdf(f"**Empleados Disponibles:** {empleados}", 'BodyText', styles))
        story.append(_format_paragraph_pdf(f"**Número de Estaciones Definidas:** {len(estaciones_data_input)}", 'BodyText', styles))
        story.append(Spacer(1, 0.1 * inch))
        
        story.append(Paragraph("Configuración de Estaciones:", styles['SectionTitle']))
        for est_d in estaciones_data_input:
            pred_i = f" (Predecesora: {est_d['predecesora']})" if est_d.get('predecesora') else ""
            story.append(Paragraph(f"&bull; {est_d['nombre']}: Tiempo {est_d.get('tiempo',0):.2f} min{pred_i}", styles['BulletPoint']))
        story.append(Spacer(1, 0.2 * inch))
        
        story.append(Paragraph("2. Resultados del Método de la Ruta Crítica (CPM)", styles['SubTitle']))
        story.append(_format_paragraph_pdf(f"**Tiempo Total del Proyecto (Camino Crítico):** {tiempo_total_camino_critico:.2f} minutos", 'BodyText', styles))
        crit_est_str_pdf = ', '.join(camino_critico_nombres) if camino_critico_nombres else 'N/A'
        story.append(_format_paragraph_pdf(f"**Estaciones en el Camino Crítico:** {crit_est_str_pdf}", 'BodyText', styles))
        story.append(Spacer(1, 0.1 * inch))
        
        story.append(Paragraph("Detalle Completo de Estaciones (CPM):", styles['SectionTitle']))
        cpm_table_data = [["Estación", "T (min)", "ES", "EF", "LS", "LF", "Holgura", "Crítica?"]]
        for est_obj in estaciones_cpm_detalle: 
            cpm_table_data.append([
                est_obj.get('nombre','N/A'), f"{est_obj.get('tiempo',0):.2f}", f"{est_obj.get('es',0):.2f}", f"{est_obj.get('ef',0):.2f}",
                f"{est_obj.get('ls',0):.2f}", f"{est_obj.get('lf',0):.2f}", f"{est_obj.get('holgura',0):.2f}", "Sí" if est_obj.get('es_critica',False) else "No"
            ])
        
        cpm_table = Table(cpm_table_data, colWidths=[1.5*inch, 0.7*inch, 0.7*inch, 0.7*inch, 0.7*inch, 0.7*inch, 0.7*inch, 0.8*inch])
        cpm_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#4CAF50")), 
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 10),
            ('BOTTOMPADDING', (0,0), (-1,0), 8),
            ('BACKGROUND', (0,1), (-1,-1), colors.HexColor("#E8F5E9")), 
            ('GRID', (0,0), (-1,-1), 1, colors.darkgrey),
            ('FONTSIZE', (0,1), (-1,-1), 9),
        ]))
        story.append(cpm_table)
        story.append(Spacer(1, 0.2 * inch))

        story.append(Paragraph("3. Métricas Clave de Producción", styles['SubTitle']))
        metricas_principales = [
            f"**Eficiencia de la Línea:** {eficiencia_linea:.2f}%",
            f"**Cuello de Botella:** '{cuello_botella_info.get('nombre','N/A')}' (Tiempo Proceso: {cuello_botella_info.get('tiempo_proceso_individual',0):.2f} min)",
            f"**Tiempo de Ciclo (Promedio Proyecto):** {tiempo_ciclo_calculado:.2f} min/unidad (para {unidades} unidades)",
            f"**Tiempo de Ciclo de Línea (Takt Time):** {cuello_botella_info.get('tiempo_proceso_individual',0):.2f} min/unidad",
            f"**Tiempo Total de Producción Estimado:** {tiempo_produccion_total_estimado:.2f} minutos (para {unidades} unidades)"
        ]
        for metrica in metricas_principales:
            story.append(_format_paragraph_pdf(f"- {metrica}", 'BodyText', styles))
        story.append(Spacer(1, 0.2 * inch))

        story.append(Paragraph("4. Asignación de Empleados", styles['SubTitle']))
        if empleados_asignados_por_estacion:
            for item_emp in empleados_asignados_por_estacion:
                story.append(Paragraph(f"&bull; Estación '{item_emp['nombre']}': {item_emp['empleados']} empleado(s)", styles['BulletPoint']))
            suma_asignados_pdf = sum(item_emp['empleados'] for item_emp in empleados_asignados_por_estacion)
            story.append(_format_paragraph_pdf(f"**Total Empleados Asignados:** {suma_asignados_pdf}", 'BoldText', styles))
        else:
            story.append(_format_paragraph_pdf("No se realizó asignación de empleados.", 'BodyText', styles))
        story.append(Spacer(1, 0.2 * inch))

        story.append(Paragraph("5. Análisis General y Recomendaciones", styles['SubTitle']))
        inicio_recomendaciones = analisis_texto_completo.find("**IV. RECOMENDACIONES GENERALES:**")
        if inicio_recomendaciones != -1:
            partes_analisis = analisis_texto_completo.split("**IV. RECOMENDACIONES GENERALES:**")
            story.append(_format_paragraph_pdf(f"La **eficiencia** calculada de la línea es del **{eficiencia_linea:.2f}%**. ", 'BodyText', styles))
            if cuello_botella_info.get("nombre") and cuello_botella_info.get("nombre") != "N/A":
                 story.append(_format_paragraph_pdf(f"El **cuello de botella** principal es la estación **'{cuello_botella_info.get('nombre')}'** con un tiempo de proceso de **{cuello_botella_info.get('tiempo_proceso_individual',0):.2f} minutos**.", 'BodyText', styles))
            
            if len(partes_analisis) > 1:
                recomendaciones_texto = partes_analisis[1]
                story.append(Paragraph("Recomendaciones Clave:", styles['SectionTitle']))
                for linea_rec in recomendaciones_texto.split('\n'):
                    linea_limpia_rec = linea_rec.strip()
                    if linea_limpia_rec and not linea_limpia_rec.startswith("Nota:") and not linea_limpia_rec.startswith("="):
                         if linea_limpia_rec.startswith("- "):
                            story.append(_format_paragraph_pdf(f"&bull; {linea_limpia_rec[2:]}", 'BulletPoint', styles))
                         else:
                            story.append(_format_paragraph_pdf(linea_limpia_rec, 'BodyText', styles))
        else: 
            story.append(_format_paragraph_pdf(analisis_texto_completo, 'BodyText', styles))
        story.append(Spacer(1, 0.1 * inch))
        story.append(_format_paragraph_pdf("<i>Nota: Este es un análisis basado en los datos proporcionados. Se recomienda una validación en campo.</i>", 'SmallText', styles))
        story.append(Spacer(1, 0.2 * inch))
        
        added_charts = False
        if grafico_pie_url_rel:
            full_pie_path_pdf = os.path.join(TEMP_CHART_DIR, os.path.basename(grafico_pie_url_rel))
            if os.path.exists(full_pie_path_pdf):
                try:
                    story.append(Paragraph("Gráfico: Distribución del Tiempo por Estación", styles['SectionTitle']))
                    img_p = Image(full_pie_path_pdf, width=5*inch, height=5*inch) 
                    img_p.hAlign = 'CENTER'
                    story.append(img_p)
                    story.append(Spacer(1, 0.1 * inch))
                    added_charts = True
                except Exception as e_img_p:
                    print(f"Error al añadir gráfico de pastel al PDF desde URL: {e_img_p}")
                    story.append(Paragraph(f"[Error al cargar gráfico de pastel: {e_img_p}]", styles['SmallText']))
            else:
                print(f"WARN: Archivo de gráfico pastel no encontrado en PDF: {full_pie_path_pdf}")

        if grafico_barras_url_rel:
            full_bar_path_pdf = os.path.join(TEMP_CHART_DIR, os.path.basename(grafico_barras_url_rel))
            if os.path.exists(full_bar_path_pdf):
                try:
                    story.append(Paragraph("Gráfico: Asignación de Empleados por Estación", styles['SectionTitle']))
                    img_b = Image(full_bar_path_pdf, width=6.5*inch, height=4*inch) 
                    img_b.hAlign = 'CENTER'
                    story.append(img_b)
                    story.append(Spacer(1, 0.1 * inch))
                    added_charts = True
                except Exception as e_img_b:
                    print(f"Error al añadir gráfico de barras al PDF desde URL: {e_img_b}")
                    story.append(Paragraph(f"[Error al cargar gráfico de barras: {e_img_b}]", styles['SmallText']))
            else:
                print(f"WARN: Archivo de gráfico de barras no encontrado en PDF: {full_bar_path_pdf}")
        
        if not added_charts and (grafico_pie_url_rel or grafico_barras_url_rel) :
             story.append(Paragraph("Algunos gráficos no pudieron ser cargados (archivo no encontrado o error).", styles['BodyText']))
        elif not grafico_pie_url_rel and not grafico_barras_url_rel:
            story.append(Paragraph("No se generaron gráficos para este reporte.", styles['BodyText']))

        doc.build(story)
        buffer.seek(0)
        
        # Notificación de generación de PDF
        mensaje_pdf_generado = (
            f"📄 ¡Reporte PDF Generado!\n"
            f"El reporte para {unidades} unidades y {empleados} empleados está listo para descargarse."
        )
        enviar_alerta_balanceo_whatsapp(mensaje_pdf_generado)
        
        return send_file(buffer, as_attachment=True, download_name='Reporte_Balanceo_Linea_Productiva.pdf', mimetype='application/pdf')

    except ValueError as ve_pdf: 
        return jsonify({"error": f"Error en los datos para el reporte PDF: {str(ve_pdf)}"}), 400
    except Exception as e_pdf: 
        import traceback
        print(f"ERROR FATAL en /api/line_balance/report/pdf: {traceback.format_exc()}")
        enviar_alerta_balanceo_whatsapp(f"❌ ERROR Crítico al generar PDF: {type(e_pdf).__name__} - {str(e_pdf)}")
        return jsonify({"error": f"Error interno del servidor al generar PDF: {str(e_pdf)}. Consulte los logs."}), 500

@app.route('/api/line_balance/report/txt', methods=['POST'])
def generate_txt_report_api():
    try:
        payload_txt = request.get_json()
        if not payload_txt:
            return jsonify({"error": "No se recibieron datos para generar el reporte TXT."}), 400

        input_data_txt = payload_txt.get('input_data_original', {})
        results_txt = payload_txt.get('resultados_calculados', {})

        if not input_data_txt or not results_txt:
            return jsonify({"error": "Datos para el reporte TXT son incompletos."}), 400
            
        unidades_txt = input_data_txt.get('unidades_a_producir',0)
        empleados_txt = input_data_txt.get('num_empleados_disponibles',0)
        estaciones_data_input_txt = input_data_txt.get('estaciones_data', [])
        analisis_completo_txt = results_txt.get("analisis_texto", "Análisis no disponible.")
        
        contenido_txt = f"REPORTE DE BALANCEO DE LÍNEAS DE PRODUCCIÓN (FORMATO TEXTO)\n"
        contenido_txt += f"Fecha de Generación: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        contenido_txt += "="*70 + "\n\n"
        contenido_txt += f"PARÁMETROS DE SIMULACIÓN:\n"
        contenido_txt += f"- Unidades a Producir: {unidades_txt}\n"
        contenido_txt += f"- Empleados Disponibles: {empleados_txt}\n"
        contenido_txt += f"- Estaciones Definidas: {len(estaciones_data_input_txt)}\n\n"
        
        contenido_txt += "CONFIGURACIÓN DE ESTACIONES:\n"
        for est_d_txt in estaciones_data_input_txt:
            pred_i_txt = f" (Predecesora: {est_d_txt.get('predecesora','')})" if est_d_txt.get('predecesora') else ""
            contenido_txt += f"  - {est_d_txt.get('nombre','N/A')}: Tiempo {est_d_txt.get('tiempo',0):.2f} min{pred_i_txt}\n"
        contenido_txt += "\n" + "="*70 + "\n\n"
        
        contenido_txt += analisis_completo_txt 

        buffer_txt = BytesIO(contenido_txt.encode('utf-8'))
        buffer_txt.seek(0)

        # Notificación de generación de TXT
        mensaje_txt_generado = (
            f"� ¡Reporte TXT Generado!\n"
            f"El reporte para {unidades_txt} unidades y {empleados_txt} empleados está listo para descargarse."
        )
        enviar_alerta_balanceo_whatsapp(mensaje_txt_generado)

        return send_file(buffer_txt, as_attachment=True, download_name='Reporte_Balanceo_Linea_Productiva.txt', mimetype='text/plain; charset=utf-8')

    except ValueError as ve_txt:
        return jsonify({"error": f"Error en los datos para el reporte TXT: {str(ve_txt)}"}), 400
    except Exception as e_txt:
        import traceback
        print(f"ERROR en /api/line_balance/report/txt: {traceback.format_exc()}")
        enviar_alerta_balanceo_whatsapp(f"❌ ERROR Crítico al generar TXT: {type(e_txt).__name__} - {str(e_txt)}")
        return jsonify({"error": f"Error interno del servidor generando TXT: {str(e_txt)}"}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5001))
    print(f"INFO: Iniciando aplicación Flask para Balanceo de Líneas en el puerto {port}...")
    print(f"INFO: Directorio de gráficos temporales: {TEMP_CHART_DIR}")
    if not twilio_client:
         print("\n" + "*"*70 +
              "\nADVERTENCIA: Cliente de Twilio no inicializado." +
              "\n             Las alertas de WhatsApp NO funcionarán." +
              "\n             Verifique las credenciales de Twilio (ACCOUNT_SID, AUTH_TOKEN, etc.) " +
              "\n             (idealmente como variables de entorno o las proporcionadas en el código) y reinicie la aplicación.\n" +
              "             Asegúrese también de que el número de WhatsApp de destino esté vinculado al Sandbox de Twilio si lo está usando.\n" + "*"*70 + "\n")
    app.run(debug=True, port=port)