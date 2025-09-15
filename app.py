# -*- coding: utf-8 -*-
"""
Aplicación Streamlit para el Balanceo de Líneas de Producción.

Esta aplicación reimplementa una herramienta web originalmente construida con Flask
para proporcionar un análisis interactivo del balanceo de líneas, cálculo de CPM,
métricas de eficiencia y asignación de recursos, todo dentro de un entorno Streamlit.

Para desplegar en Streamlit Community Cloud, asegúrese de incluir las credenciales
de Twilio como "Secrets" si desea utilizar las notificaciones de WhatsApp.
Ejemplo de secrets.toml:
TWILIO_ACCOUNT_SID = "AC..."
TWILIO_AUTH_TOKEN = "..."
TWILIO_WHATSAPP_FROM_NUMBER = "+14155238886"
DESTINATION_WHATSAPP_NUMBER = "+57..."
"""
import streamlit as st
import datetime
import os
import matplotlib
matplotlib.use('Agg') # Backend para entornos sin GUI
import matplotlib.pyplot as plt
import numpy as np
from io import BytesIO
import re

# --- Importaciones para PDF con ReportLab ---
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors

# --- Lógica de Negocio (Clases Estacion y LineaProduccion) ---
# (Estas clases se han adaptado ligeramente para un mejor logging y manejo de errores en Streamlit)

class Estacion:
    """
    Representa una estación de trabajo en la línea de producción.
    Almacena información sobre su tiempo de proceso y relaciones de precedencia.
    También guarda los resultados del cálculo CPM (ES, EF, LS, LF, Holgura).
    """
    def __init__(self, nombre, tiempo, predecesora_nombre=""):
        self.nombre = nombre
        if not isinstance(tiempo, (int, float)) or tiempo <= 0:
            raise ValueError(f"El tiempo para la estación '{nombre}' debe ser un número positivo. Recibido: {tiempo}")
        self.tiempo = float(tiempo)
        self.predecesora_nombre = predecesora_nombre
        self.es = 0.0  # Earliest Start
        self.ef = 0.0  # Earliest Finish
        self.ls = 0.0  # Latest Start
        self.lf = 0.0  # Latest Finish
        self.holgura = 0.0
        self.es_critica = False

    def __repr__(self):
        return (f"Estacion(Nombre: {self.nombre}, Tiempo: {self.tiempo:.2f}, Pred: '{self.predecesora_nombre}')")

class LineaProduccion:
    """
    Gestiona el conjunto de estaciones, los parámetros de producción y realiza los cálculos
    de CPM, métricas de eficiencia, asignación de empleados y generación de análisis.
    """
    def __init__(self, estaciones_data, unidades_a_producir, num_empleados_disponibles):
        self.estaciones_dict = {}
        self.estaciones_lista = []
        self._procesar_estaciones_data(estaciones_data)
        
        if not isinstance(unidades_a_producir, int) or unidades_a_producir < 0:
            raise ValueError("Las unidades a producir deben ser un número entero no negativo.")
        self.unidades_a_producir = unidades_a_producir
        
        if not isinstance(num_empleados_disponibles, int) or num_empleados_disponibles < 0:
            raise ValueError("El número de empleados disponibles debe ser un número entero no negativo.")
        self.num_empleados_disponibles = num_empleados_disponibles
        
        self.tiempo_total_camino_critico = 0.0
        self.camino_critico_nombres = []
        self.tiempo_ciclo_calculado = 0.0
        self.tiempo_produccion_total_estimado = 0.0
        self.eficiencia_linea = 0.0
        self.cuello_botella_info = {"nombre": "", "tiempo_acumulado": 0.0, "tipo": ""}
        self.empleados_asignados_por_estacion = []

    def _procesar_estaciones_data(self, estaciones_data):
        if not estaciones_data:
            raise ValueError("No se proporcionaron datos de estaciones.")
        
        nombres_vistos = set()
        for i, data in enumerate(estaciones_data):
            nombre = data.get("nombre")
            tiempo = data.get("tiempo")
            predecesora = data.get("predecesora", "")

            if not nombre:
                raise ValueError(f"La estación #{i+1} no tiene nombre.")
            if nombre.lower() in nombres_vistos:
                raise ValueError(f"Nombre de estación duplicado: '{nombre}'. Los nombres deben ser únicos.")
            nombres_vistos.add(nombre.lower())

            est = Estacion(nombre, tiempo, predecesora)
            self.estaciones_dict[est.nombre] = est
            self.estaciones_lista.append(est)

        for est in self.estaciones_lista:
            if est.predecesora_nombre and est.predecesora_nombre not in self.estaciones_dict:
                raise ValueError(f"La predecesora '{est.predecesora_nombre}' para la estación '{est.nombre}' no existe.")

    def calcular_cpm(self):
        if not self.estaciones_lista: return

        # Forward pass para ES y EF
        for est in self.estaciones_lista:
            if not est.predecesora_nombre:
                est.es = 0
            else:
                pred = self.estaciones_dict[est.predecesora_nombre]
                est.es = pred.ef
            est.ef = est.es + est.tiempo
        
        self.tiempo_total_camino_critico = max((est.ef for est in self.estaciones_lista), default=0.0)

        # Backward pass para LS y LF
        for est in reversed(self.estaciones_lista):
            sucesores = [s for s in self.estaciones_lista if s.predecesora_nombre == est.nombre]
            if not sucesores:
                est.lf = self.tiempo_total_camino_critico
            else:
                est.lf = min(s.ls for s in sucesores)
            est.ls = est.lf - est.tiempo

        # Holgura y camino crítico
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

    def calcular_metricas_produccion(self):
        if not self.estaciones_lista: return

        tiempo_estacion_mas_larga = self.cuello_botella_info.get("tiempo_proceso_individual", 0)
        
        self.tiempo_ciclo_calculado = tiempo_estacion_mas_larga
        
        if self.unidades_a_producir > 0 and tiempo_estacion_mas_larga > 0:
            self.tiempo_produccion_total_estimado = self.tiempo_total_camino_critico + (self.unidades_a_producir - 1) * tiempo_estacion_mas_larga
        else:
            self.tiempo_produccion_total_estimado = self.tiempo_total_camino_critico

        sum_tiempos_individuales_tareas = sum(est.tiempo for est in self.estaciones_lista)
        
        if self.num_empleados_disponibles > 0 and tiempo_estacion_mas_larga > 0:
            denominador_eficiencia = len(self.estaciones_lista) * tiempo_estacion_mas_larga
            self.eficiencia_linea = (sum_tiempos_individuales_tareas / denominador_eficiencia) * 100
        else:
            self.eficiencia_linea = 0.0

    def asignar_empleados(self):
        if not self.estaciones_lista or self.num_empleados_disponibles == 0:
            self.empleados_asignados_por_estacion = [{"nombre": est.nombre, "empleados": 0} for est in self.estaciones_lista]
            return

        total_tiempo_tareas = sum(est.tiempo for est in self.estaciones_lista)
        if total_tiempo_tareas == 0: return

        # Asignación proporcional
        asignaciones = []
        for est in self.estaciones_lista:
            proporcion = est.tiempo / total_tiempo_tareas
            empleados_ideal = proporcion * self.num_empleados_disponibles
            asignaciones.append({
                "nombre": est.nombre,
                "ideal": empleados_ideal,
                "base": int(empleados_ideal),
                "fraccion": empleados_ideal - int(empleados_ideal)
            })

        empleados_asignados = sum(a['base'] for a in asignaciones)
        restantes = self.num_empleados_disponibles - empleados_asignados
        
        asignaciones.sort(key=lambda x: x['fraccion'], reverse=True)

        for i in range(restantes):
            asignaciones[i]['base'] += 1

        mapa_asignacion = {a['nombre']: a['base'] for a in asignaciones}
        self.empleados_asignados_por_estacion = [
            {"nombre": est.nombre, "empleados": mapa_asignacion.get(est.nombre, 0)}
            for est in self.estaciones_lista
        ]

    def generar_texto_analisis_resultados(self):
        analisis = f"### Análisis de Resultados (para {self.unidades_a_producir} unidades y {self.num_empleados_disponibles} empleados)\n\n"
        
        # CPM
        analisis += f"**Ruta Crítica (CPM):**\n"
        analisis += f"- **Tiempo Total del Proyecto:** `{self.tiempo_total_camino_critico:.2f}` minutos.\n"
        crit_est_str = ', '.join(self.camino_critico_nombres) if self.camino_critico_nombres else 'N/A'
        analisis += f"- **Estaciones Críticas:** `{crit_est_str}`\n\n"
        
        # Métricas
        analisis += f"**Métricas de Producción:**\n"
        analisis += f"- **Eficiencia de la Línea:** `{self.eficiencia_linea:.2f}%`\n"
        cb_nombre = self.cuello_botella_info.get('nombre', 'N/A')
        cb_tiempo = self.cuello_botella_info.get('tiempo_proceso_individual', 0)
        analisis += f"- **Cuello de Botella:** Estación `'{cb_nombre}'` con `{cb_tiempo:.2f}` minutos.\n"
        analisis += f"- **Tiempo de Ciclo (Takt Time):** `{self.tiempo_ciclo_calculado:.2f}` minutos/unidad.\n"
        analisis += f"- **Tiempo Total de Producción Estimado:** `{self.tiempo_produccion_total_estimado:.2f}` minutos.\n\n"

        # Asignación de Empleados
        analisis += "**Asignación de Empleados Sugerida:**\n"
        if self.empleados_asignados_por_estacion:
            for asignacion in self.empleados_asignados_por_estacion:
                analisis += f"- Estación `'{asignacion['nombre']}'`: `{asignacion['empleados']}` empleado(s).\n"
        else:
            analisis += "- No se realizó asignación de empleados.\n"
        
        # Recomendaciones
        analisis += "\n**Recomendaciones:**\n"
        if self.eficiencia_linea < 75:
            analisis += f"- **Revisar Carga de Trabajo:** La eficiencia es moderada/baja. Considerar redistribuir tareas desde el cuello de botella (`{cb_nombre}`) hacia estaciones con más holgura.\n"
        else:
            analisis += "- **Buen Balance:** La eficiencia es alta. Mantener monitoreo continuo para mejoras incrementales.\n"
        analisis += "- **Flexibilidad:** Fomentar la capacitación cruzada (cross-training) de empleados para aumentar la flexibilidad de la línea.\n"

        return analisis

# --- Funciones de Generación de Gráficos y Reportes ---

def generar_graficos(linea_obj):
    """Genera y devuelve los objetos de figura de Matplotlib."""
    fig_pie, fig_bar = None, None

    # Gráfico de Pastel
    if linea_obj.estaciones_lista and sum(est.tiempo for est in linea_obj.estaciones_lista) > 0:
        nombres = [est.nombre for est in linea_obj.estaciones_lista]
        tiempos = [est.tiempo for est in linea_obj.estaciones_lista]
        
        fig_pie, ax1 = plt.subplots(figsize=(6, 4))
        ax1.pie(tiempos, labels=nombres, autopct='%1.1f%%', startangle=90)
        ax1.axis('equal')
        ax1.set_title('Distribución del Tiempo por Estación')
        plt.tight_layout()

    # Gráfico de Barras
    if linea_obj.empleados_asignados_por_estacion:
        nombres = [a['nombre'] for a in linea_obj.empleados_asignados_por_estacion]
        empleados = [a['empleados'] for a in linea_obj.empleados_asignados_por_estacion]

        fig_bar, ax2 = plt.subplots(figsize=(6, 4))
        ax2.bar(nombres, empleados)
        ax2.set_xlabel('Estación')
        ax2.set_ylabel('Empleados Asignados')
        ax2.set_title('Asignación de Empleados')
        plt.xticks(rotation=45, ha="right")
        plt.tight_layout()

    return fig_pie, fig_bar

def generar_reporte_txt(results):
    """Genera el contenido de un reporte TXT en memoria."""
    linea = results['linea_obj']
    analisis_texto = linea.generar_texto_analisis_resultados().replace("`", "").replace("*", "").replace("#", "")
    
    # Recrear tabla CPM para TXT
    cpm_header = f"{'Estación':<20} | {'Tiempo':>7} | {'ES':>7} | {'EF':>7} | {'LS':>7} | {'LF':>7} | {'Holgura':>7} | {'Crítica':>8}\n"
    cpm_header += "-" * len(cpm_header) + "\n"
    cpm_rows = ""
    for est in linea.estaciones_lista:
        cpm_rows += f"{est.nombre:<20} | {est.tiempo:7.2f} | {est.es:7.2f} | {est.ef:7.2f} | {est.ls:7.2f} | {est.lf:7.2f} | {est.holgura:7.2f} | {'Sí' if est.es_critica else 'No':>8}\n"

    contenido_txt = f"REPORTE DE BALANCEO DE LÍNEA\n"
    contenido_txt += f"Fecha: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    contenido_txt += "="*70 + "\n\n"
    contenido_txt += analisis_texto
    contenido_txt += "\n\nDETALLE CPM\n" + cpm_header + cpm_rows
    
    return contenido_txt.encode('utf-8')

# (Las funciones de PDF y Twilio se omiten por brevedad en este ejemplo,
# pero su lógica se puede integrar de manera similar si es necesario.)
USER_TWILIO_ACCOUNT_SID = "AC54b60c4a414f9e4ce112680d5b453578"
USER_TWILIO_AUTH_TOKEN = "455508d7d3c49b4046e97ac934e606f1"
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

# --- Interfaz de Streamlit ---

st.set_page_config(page_title="Optimización de Líneas", layout="wide", page_icon="⚙️")

st.title("⚙️ Optimización de Líneas de Producción")
st.markdown("Herramienta avanzada para el análisis y balanceo eficiente de sus procesos productivos.")

# --- Inicialización del Estado ---
if 'estaciones' not in st.session_state:
    st.session_state.estaciones = [
        {'nombre': 'Corte', 'tiempo': 2.0, 'predecesora': ''},
        {'nombre': 'Doblado', 'tiempo': 3.0, 'predecesora': 'Corte'},
        {'nombre': 'Ensamblaje', 'tiempo': 5.0, 'predecesora': 'Doblado'},
        {'nombre': 'Pintura', 'tiempo': 4.0, 'predecesora': 'Ensamblaje'},
        {'nombre': 'Empaque', 'tiempo': 1.5, 'predecesora': 'Pintura'}
    ]
if 'results' not in st.session_state:
    st.session_state.results = None

# --- Barra Lateral de Entradas ---
with st.sidebar:
    st.header("1. Parámetros Globales")
    unidades = st.number_input("Unidades a Producir", min_value=1, value=100, step=10)
    empleados = st.number_input("Empleados Disponibles", min_value=1, value=5, step=1)
    
    st.header("2. Configuración de Estaciones")
    num_estaciones = st.number_input("Número de Estaciones", min_value=1, value=len(st.session_state.estaciones), key="num_est")

    # Ajustar el tamaño de la lista de estaciones en el estado
    current_len = len(st.session_state.estaciones)
    if num_estaciones > current_len:
        for _ in range(num_estaciones - current_len):
            st.session_state.estaciones.append({'nombre': '', 'tiempo': 1.0, 'predecesora': ''})
    elif num_estaciones < current_len:
        st.session_state.estaciones = st.session_state.estaciones[:num_estaciones]
    
    # Crear inputs para cada estación
    nombres_posibles_predecesoras = [""] + [st.session_state.estaciones[i]['nombre'] for i in range(num_estaciones) if st.session_state.estaciones[i]['nombre']]

    for i in range(num_estaciones):
        with st.expander(f"Estación {i+1}: {st.session_state.estaciones[i]['nombre'] or 'Nueva'}", expanded=True):
            st.session_state.estaciones[i]['nombre'] = st.text_input(f"Nombre Estación {i+1}", value=st.session_state.estaciones[i]['nombre'], key=f"nombre_{i}")
            st.session_state.estaciones[i]['tiempo'] = st.number_input(f"Tiempo (min) {i+1}", min_value=0.01, value=st.session_state.estaciones[i]['tiempo'], key=f"tiempo_{i}")
            
            # Predecesora con Selectbox
            # Actualizar lista de predecesoras para el selectbox actual
            predecesoras_disponibles = [""] + [est['nombre'] for j, est in enumerate(st.session_state.estaciones) if i != j and est['nombre']]
            
            # Si la predecesora guardada no está en la lista, añadirla temporalmente
            current_pred = st.session_state.estaciones[i]['predecesora']
            if current_pred and current_pred not in predecesoras_disponibles:
                predecesoras_disponibles.append(current_pred)

            try:
                idx = predecesoras_disponibles.index(current_pred)
            except ValueError:
                idx = 0 # Default a "" si hay algún problema
            
            st.session_state.estaciones[i]['predecesora'] = st.selectbox(f"Predecesora {i+1}", options=predecesoras_disponibles, index=idx, key=f"pred_{i}")

# --- Botón de Cálculo Principal ---
if st.sidebar.button("Calcular Balanceo", type="primary", use_container_width=True):
    with st.spinner("Realizando cálculos..."):
        try:
            # Validación de datos de entrada
            nombres_unicos = {est['nombre'].lower() for est in st.session_state.estaciones if est['nombre']}
            if len(nombres_unicos) != len([est['nombre'] for est in st.session_state.estaciones if est['nombre']]):
                st.error("Error: Existen nombres de estación duplicados. Por favor, corríjalos.")
            elif any(not est['nombre'] or est['tiempo'] <= 0 for est in st.session_state.estaciones):
                st.error("Error: Todas las estaciones deben tener un nombre y un tiempo positivo.")
            else:
                linea = LineaProduccion(st.session_state.estaciones, unidades, empleados)
                linea.calcular_cpm()
                linea.calcular_metricas_produccion()
                linea.asignar_empleados()
                
                fig_pie, fig_bar = generar_graficos(linea)

                st.session_state.results = {
                    "linea_obj": linea,
                    "fig_pie": fig_pie,
                    "fig_bar": fig_bar
                }
                st.success("¡Cálculo completado exitosamente!")

        except ValueError as e:
            st.error(f"Error de validación: {e}")
        except Exception as e:
            st.error(f"Ocurrió un error inesperado: {e}")

# --- Área de Resultados ---
if st.session_state.results:
    linea_res = st.session_state.results['linea_obj']
    
    st.header("3. Acciones y Resultados")
    
    # Botones de descarga
    col1, col2 = st.columns(2)
    with col1:
        txt_data = generar_reporte_txt(st.session_state.results)
        st.download_button(
            label="📄 Exportar a TXT",
            data=txt_data,
            file_name="reporte_balanceo.txt",
            mime="text/plain",
            use_container_width=True
        )
    with col2:
        st.info("La exportación a PDF está en desarrollo.", icon="⏳")

    # Pestañas de resultados
    tab_analisis, tab_cpm, tab_graficos = st.tabs(["📊 Análisis y Métricas", "📈 Detalle CPM", "🎨 Gráficos"])

    with tab_analisis:
        st.markdown(linea_res.generar_texto_analisis_resultados())

    with tab_cpm:
        st.subheader("Detalle de Estaciones (Método de la Ruta Crítica)")
        cpm_data = []
        for est in linea_res.estaciones_lista:
            cpm_data.append({
                "Estación": est.nombre,
                "Tiempo": est.tiempo,
                "ES": est.es,
                "EF": est.ef,
                "LS": est.ls,
                "LF": est.lf,
                "Holgura": est.holgura,
                "Crítica": "Sí" if est.es_critica else "No"
            })
        st.dataframe(cpm_data, use_container_width=True)

    with tab_graficos:
        st.subheader("Visualización Gráfica")
        fig_p = st.session_state.results['fig_pie']
        fig_b = st.session_state.results['fig_bar']
        
        if fig_p or fig_b:
            col_g1, col_g2 = st.columns(2)
            with col_g1:
                if fig_p:
                    st.pyplot(fig_p)
                else:
                    st.info("No hay datos para el gráfico de distribución de tiempo.")
            with col_g2:
                if fig_b:
                    st.pyplot(fig_b)
                else:
                    st.info("No hay datos para el gráfico de asignación de empleados.")
        else:
            st.warning("No se pudieron generar gráficos con los datos proporcionados.")
else:
    st.info("Ingrese los parámetros en la barra lateral y presione 'Calcular Balanceo' para ver los resultados.")

