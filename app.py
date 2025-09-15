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
import matplotlib
matplotlib.use('Agg') # Backend para entornos sin GUI
import matplotlib.pyplot as plt
from io import BytesIO

# --- Importación de Twilio ---
# Se envuelve en un try-except para que la app funcione incluso si twilio no está instalado localmente.
try:
    from twilio.rest import Client
    IS_TWILIO_AVAILABLE = True
except ImportError:
    IS_TWILIO_AVAILABLE = False


# --- Lógica de Negocio (Clases Estacion y LineaProduccion) ---
# (Se mantienen las clases originales sin cambios en su lógica principal)

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

# --- Lógica de Twilio con Streamlit Secrets ---
LOW_EFFICIENCY_THRESHOLD = 75 # Umbral para enviar alerta

def inicializar_twilio_client():
    """
    Inicializa el cliente de Twilio usando las credenciales de st.secrets.
    Devuelve el cliente si las credenciales son válidas, de lo contrario devuelve None.
    """
    if not IS_TWILIO_AVAILABLE:
        return None

    try:
        # Verifica que todas las claves secretas necesarias existan
        required_secrets = ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM_NUMBER", "DESTINATION_WHATSAPP_NUMBER"]
        if all(hasattr(st, 'secrets') and key in st.secrets for key in required_secrets):
            account_sid = st.secrets["TWILIO_ACCOUNT_SID"]
            auth_token = st.secrets["TWILIO_AUTH_TOKEN"]
            
            if account_sid.startswith("AC") and len(auth_token) == 32:
                client = Client(account_sid, auth_token)
                st.session_state.twilio_configured = True
                return client
            else:
                # Muestra una advertencia si el formato es incorrecto
                if 'twilio_warning_shown' not in st.session_state:
                    st.warning("Credenciales de Twilio parecen tener un formato incorrecto. Las notificaciones están desactivadas.", icon="⚠️")
                    st.session_state.twilio_warning_shown = True # Evita mostrarlo en cada recarga
                st.session_state.twilio_configured = False
                return None
        else:
            st.session_state.twilio_configured = False
            return None
    except Exception as e:
        if 'twilio_error_shown' not in st.session_state:
            st.error(f"Error al inicializar el cliente de Twilio: {e}")
            st.session_state.twilio_error_shown = True
        st.session_state.twilio_configured = False
        return None

def enviar_alerta_balanceo_whatsapp(mensaje):
    """
    Envía una alerta por WhatsApp si el cliente de Twilio está configurado.
    """
    if not st.session_state.get('twilio_configured', False) or st.session_state.twilio_client is None:
        return False
    
    try:
        from_number = st.secrets["TWILIO_WHATSAPP_FROM_NUMBER"]
        to_number = st.secrets["DESTINATION_WHATSAPP_NUMBER"]

        message_instance = st.session_state.twilio_client.messages.create(
            from_=f'whatsapp:{from_number}',
            body=mensaje,
            to=f'whatsapp:{to_number}'
        )
        st.toast(f"¡Alerta de WhatsApp enviada a {to_number}!", icon="✅")
        return True
    except Exception as e:
        st.error(f"Error crítico al enviar la alerta de WhatsApp: {e}")
        return False

# --- Interfaz de Streamlit ---

st.set_page_config(page_title="Optimización de Líneas", layout="wide", page_icon="⚙️")

# --- Inicialización del Cliente de Twilio ---
if 'twilio_client' not in st.session_state:
    st.session_state.twilio_client = inicializar_twilio_client()

st.title("⚙️ Optimización de Líneas de Producción")
st.markdown("Herramienta avanzada para el análisis y balanceo eficiente de sus procesos productivos.")

# --- Inicialización del Estado de la Sesión ---
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
    for i in range(num_estaciones):
        with st.expander(f"Estación {i+1}: {st.session_state.estaciones[i]['nombre'] or 'Nueva'}", expanded=True):
            st.session_state.estaciones[i]['nombre'] = st.text_input(f"Nombre Estación {i+1}", value=st.session_state.estaciones[i]['nombre'], key=f"nombre_{i}")
            st.session_state.estaciones[i]['tiempo'] = st.number_input(f"Tiempo (min) {i+1}", min_value=0.01, value=st.session_state.estaciones[i]['tiempo'], key=f"tiempo_{i}")
            
            predecesoras_disponibles = [""] + [est['nombre'] for j, est in enumerate(st.session_state.estaciones) if i != j and est['nombre']]
            current_pred = st.session_state.estaciones[i]['predecesora']
            try:
                idx = predecesoras_disponibles.index(current_pred)
            except ValueError:
                idx = 0
            
            st.session_state.estaciones[i]['predecesora'] = st.selectbox(f"Predecesora {i+1}", options=predecesoras_disponibles, index=idx, key=f"pred_{i}")

# --- Botón de Cálculo Principal ---
if st.sidebar.button("Calcular Balanceo", type="primary", use_container_width=True):
    with st.spinner("Realizando cálculos..."):
        try:
            # Validación de datos
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
                
                # Envío de alerta si la eficiencia es baja y Twilio está configurado
                if linea.eficiencia_linea < LOW_EFFICIENCY_THRESHOLD:
                    mensaje_alerta = (
                        f"¡Alerta de Producción! 📉\n"
                        f"La eficiencia de la línea ha caído a *{linea.eficiencia_linea:.2f}%*, "
                        f"por debajo del umbral de {LOW_EFFICIENCY_THRESHOLD}%.\n\n"
                        f"Cuello de botella: Estación '{linea.cuello_botella_info.get('nombre', 'N/A')}'."
                    )
                    enviar_alerta_balanceo_whatsapp(mensaje_alerta)

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
        if st.session_state.get('twilio_configured', False):
            st.success("Notificaciones por WhatsApp activas.", icon="🔔")
        else:
            st.info("Notificaciones por WhatsApp inactivas. Configure sus 'secrets' para habilitarlas.", icon="ℹ️")

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
                "ES": est.es, "EF": est.ef,
                "LS": est.ls, "LF": est.lf,
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
