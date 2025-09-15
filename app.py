# -*- coding: utf-8 -*-
"""
Aplicación Streamlit para el Balanceo de Líneas de Producción.

Versión mejorada con interfaz de usuario potenciada, cálculo corregido
y diagnósticos avanzados para la integración con Twilio.
"""
import streamlit as st
import datetime
import matplotlib
matplotlib.use('Agg') # Backend para entornos sin GUI
import matplotlib.pyplot as plt

# --- Importación de Twilio ---
try:
    from twilio.rest import Client
    IS_TWILIO_AVAILABLE = True
except ImportError:
    IS_TWILIO_AVAILABLE = False
    Client = None

# --- Lógica de Negocio (Clases sin cambios) ---
class Estacion:
    """Representa una estación de trabajo."""
    def __init__(self, nombre, tiempo, predecesora_nombre=""):
        if not isinstance(tiempo, (int, float)) or tiempo <= 0:
            raise ValueError(f"El tiempo para la estación '{nombre}' debe ser un número positivo.")
        self.nombre = nombre
        self.tiempo = float(tiempo)
        self.predecesora_nombre = predecesora_nombre
        self.es, self.ef, self.ls, self.lf, self.holgura = 0.0, 0.0, 0.0, 0.0, 0.0
        self.es_critica = False

class LineaProduccion:
    """Gestiona los cálculos de la línea de producción."""
    def __init__(self, estaciones_data, unidades, empleados):
        self.estaciones_dict = {}
        self.estaciones_lista = []
        self._procesar_estaciones_data(estaciones_data)
        self.unidades_a_producir = unidades
        self.num_empleados_disponibles = empleados
        self.tiempo_total_camino_critico = 0.0
        self.camino_critico_nombres = []
        self.tiempo_ciclo_calculado = 0.0
        self.tiempo_produccion_total_estimado = 0.0
        self.eficiencia_linea = 0.0
        self.cuello_botella_info = {}
        self.empleados_asignados_por_estacion = []

    def _procesar_estaciones_data(self, estaciones_data):
        nombres_vistos = set()
        for data in estaciones_data:
            nombre = data.get("nombre")
            if not nombre: raise ValueError("Todas las estaciones deben tener un nombre.")
            if nombre.lower() in nombres_vistos: raise ValueError(f"Nombre de estación duplicado: '{nombre}'.")
            nombres_vistos.add(nombre.lower())
            est = Estacion(nombre, data.get("tiempo"), data.get("predecesora", ""))
            self.estaciones_lista.append(est)
            self.estaciones_dict[nombre] = est
        for est in self.estaciones_lista:
            if est.predecesora_nombre and est.predecesora_nombre not in self.estaciones_dict:
                raise ValueError(f"La predecesora '{est.predecesora_nombre}' para '{est.nombre}' no existe.")

    def calcular_cpm(self):
        for est in self.estaciones_lista:
            pred = self.estaciones_dict.get(est.predecesora_nombre)
            est.es = pred.ef if pred else 0
            est.ef = est.es + est.tiempo
        self.tiempo_total_camino_critico = max((est.ef for est in self.estaciones_lista), default=0.0)
        for est in reversed(self.estaciones_lista):
            sucesores = [s for s in self.estaciones_lista if s.predecesora_nombre == est.nombre]
            est.lf = min((s.ls for s in sucesores), default=self.tiempo_total_camino_critico)
            est.ls = est.lf - est.tiempo
            est.holgura = est.ls - est.es
            if abs(est.holgura) < 1e-6:
                est.es_critica = True
        self.camino_critico_nombres = [est.nombre for est in self.estaciones_lista if est.es_critica]
        if self.estaciones_lista:
            cuello_botella = max(self.estaciones_lista, key=lambda e: e.tiempo)
            self.cuello_botella_info = {"nombre": cuello_botella.nombre, "tiempo_proceso_individual": cuello_botella.tiempo}

    def calcular_metricas_produccion(self):
        tiempo_cuello_botella = self.cuello_botella_info.get("tiempo_proceso_individual", 0)
        self.tiempo_ciclo_calculado = tiempo_cuello_botella
        if self.unidades_a_producir > 0 and tiempo_cuello_botella > 0:
            self.tiempo_produccion_total_estimado = self.tiempo_total_camino_critico + (self.unidades_a_producir - 1) * tiempo_cuello_botella
        else:
            self.tiempo_produccion_total_estimado = self.tiempo_total_camino_critico
        sum_tiempos = sum(est.tiempo for est in self.estaciones_lista)
        denominador = len(self.estaciones_lista) * tiempo_cuello_botella
        self.eficiencia_linea = (sum_tiempos / denominador) * 100 if denominador > 0 else 0.0

    def asignar_empleados(self):
        total_tiempo = sum(est.tiempo for est in self.estaciones_lista)
        if total_tiempo == 0 or self.num_empleados_disponibles == 0:
            self.empleados_asignados_por_estacion = [{"nombre": e.nombre, "empleados": 0} for e in self.estaciones_lista]
            return
        asignaciones = [{'nombre': e.nombre, 'ideal': e.tiempo / total_tiempo * self.num_empleados_disponibles} for e in self.estaciones_lista]
        for a in asignaciones: a['base'], a['fraccion'] = int(a['ideal']), a['ideal'] - int(a['ideal'])
        restantes = self.num_empleados_disponibles - sum(a['base'] for a in asignaciones)
        asignaciones.sort(key=lambda x: x['fraccion'], reverse=True)
        for i in range(restantes): asignaciones[i]['base'] += 1
        mapa = {a['nombre']: a['base'] for a in asignaciones}
        self.empleados_asignados_por_estacion = [{"nombre": e.nombre, "empleados": mapa.get(e.nombre, 0)} for e in self.estaciones_lista]

# --- Lógica de Twilio con Diagnóstico ---
LOW_EFFICIENCY_THRESHOLD = 75

def inicializar_twilio_client():
    if not IS_TWILIO_AVAILABLE: return None
    try:
        if hasattr(st, 'secrets') and all(k in st.secrets for k in ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"]):
            account_sid = st.secrets["TWILIO_ACCOUNT_SID"]
            auth_token = st.secrets["TWILIO_AUTH_TOKEN"]
            if account_sid.startswith("AC") and len(auth_token) > 30:
                st.session_state.twilio_configured = True
                return Client(account_sid, auth_token)
    except Exception as e:
        st.error(f"Error al inicializar cliente Twilio: {e}")
    st.session_state.twilio_configured = False
    return None

def enviar_alerta_balanceo_whatsapp(mensaje):
    if not st.session_state.get('twilio_configured'): return
    try:
        from_number = st.secrets["TWILIO_WHATSAPP_FROM_NUMBER"]
        to_number = st.secrets["DESTINATION_WHATSAPP_NUMBER"]
        st.session_state.twilio_client.messages.create(from_=f'whatsapp:{from_number}', body=mensaje, to=f'whatsapp:{to_number}')
        st.toast(f"¡Alerta de WhatsApp enviada a {to_number}!", icon="✅")
    except Exception as e:
        st.error(f"Error detallado al enviar WhatsApp: {e}")

# --- Interfaz de Usuario ---
st.set_page_config(page_title="Optimización de Líneas", layout="wide", page_icon="🏭")

DEFAULT_ESTACIONES = [
    {'nombre': 'Corte', 'tiempo': 2.0, 'predecesora': ''},
    {'nombre': 'Doblado', 'tiempo': 3.0, 'predecesora': 'Corte'},
    {'nombre': 'Ensamblaje', 'tiempo': 5.0, 'predecesora': 'Doblado'},
    {'nombre': 'Pintura', 'tiempo': 4.0, 'predecesora': 'Ensamblaje'},
    {'nombre': 'Empaque', 'tiempo': 1.5, 'predecesora': 'Pintura'}
]

if 'estaciones' not in st.session_state:
    st.session_state.estaciones = DEFAULT_ESTACIONES

if 'twilio_client' not in st.session_state:
    st.session_state.twilio_client = inicializar_twilio_client()

st.title("🏭 Optimizador de Líneas de Producción")
st.markdown("Una herramienta interactiva para analizar, balancear y mejorar la eficiencia de sus procesos productivos.")

# --- Barra Lateral de Configuración ---
with st.sidebar:
    st.header("⚙️ 1. Parámetros de Simulación")
    unidades = st.number_input("Unidades a Producir", min_value=1, value=100, step=10, help="Total de unidades que se fabricarán en el lote.")
    empleados = st.number_input("Empleados Disponibles", min_value=1, value=5, step=1, help="Número total de operarios para asignar en la línea.")
    
    st.header("🏢 2. Configuración de Estaciones")
    num_estaciones = len(st.session_state.estaciones)
    
    for i in range(num_estaciones):
        with st.expander(f"Estación {i+1}: **{st.session_state.estaciones[i]['nombre'] or 'Nueva'}**", expanded=True):
            c1, c2 = st.columns(2)
            st.session_state.estaciones[i]['nombre'] = c1.text_input("Nombre", value=st.session_state.estaciones[i]['nombre'], key=f"nombre_{i}")
            st.session_state.estaciones[i]['tiempo'] = c2.number_input("Tiempo (min)", min_value=0.01, value=st.session_state.estaciones[i]['tiempo'], key=f"tiempo_{i}")
            
            predecesoras_opts = [""] + [e['nombre'] for j, e in enumerate(st.session_state.estaciones) if i != j and e['nombre']]
            current_pred = st.session_state.estaciones[i]['predecesora']
            idx = predecesoras_opts.index(current_pred) if current_pred in predecesoras_opts else 0
            st.session_state.estaciones[i]['predecesora'] = st.selectbox("Predecesora", options=predecesoras_opts, index=idx, key=f"pred_{i}")

    c1, c2 = st.columns(2)
    if c1.button("➕ Añadir Estación", use_container_width=True):
        st.session_state.estaciones.append({'nombre': '', 'tiempo': 1.0, 'predecesora': ''})
        st.rerun()
    if c2.button("➖ Quitar Última", use_container_width=True, disabled=len(st.session_state.estaciones) <= 1):
        st.session_state.estaciones.pop()
        st.rerun()

    st.header("🚀 3. Acciones")
    
    # --- CORRECCIÓN: Lógica de cálculo movida aquí ---
    if st.button("Calcular Balanceo", type="primary", use_container_width=True):
        with st.spinner("Realizando cálculos..."):
            try:
                linea = LineaProduccion(st.session_state.estaciones, unidades, empleados)
                linea.calcular_cpm()
                linea.calcular_metricas_produccion()
                linea.asignar_empleados()
                st.session_state.results = {"linea_obj": linea}
                st.success("¡Cálculo completado!")

                if linea.eficiencia_linea < LOW_EFFICIENCY_THRESHOLD:
                    mensaje = (f"¡Alerta de Producción! 📉\nLa eficiencia de la línea es de solo *{linea.eficiencia_linea:.2f}%*.\n"
                               f"Cuello de botella: '{linea.cuello_botella_info.get('nombre', 'N/A')}' con {linea.cuello_botella_info.get('tiempo_proceso_individual', 0):.2f} min.")
                    enviar_alerta_balanceo_whatsapp(mensaje)

            except ValueError as e:
                st.error(f"Error de validación: {e}")
                st.session_state.results = None
            except Exception as e:
                st.error(f"Ocurrió un error inesperado: {e}")
                st.session_state.results = None
    
    if st.button("Resetear a Valores por Defecto", use_container_width=True):
        st.session_state.estaciones = DEFAULT_ESTACIONES
        st.session_state.results = None
        st.rerun()

# --- Panel de Resultados ---
if 'results' in st.session_state and st.session_state.results:
    linea_res = st.session_state.results['linea_obj']
    
    st.header("📊 Resultados Clave (KPIs)")
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Eficiencia de Línea", f"{linea_res.eficiencia_linea:.1f}%",
                delta=f"{linea_res.eficiencia_linea - 85:.1f}% vs. Objetivo (85%)",
                help="Porcentaje del tiempo que se aprovecha productivamente. (Suma de Tiempos / (Nº Estaciones * Tiempo de Ciclo))")
    col2.metric("Tiempo de Ciclo", f"{linea_res.tiempo_ciclo_calculado:.2f} min/ud",
                help="Determinado por la estación más lenta (cuello de botella). Es el ritmo máximo de producción.")
    col3.metric("Tiempo Total Estimado", f"{linea_res.tiempo_produccion_total_estimado:.1f} min",
                help=f"Tiempo estimado para producir las {unidades} unidades solicitadas.")

    tab1, tab2, tab3 = st.tabs(["📈 Análisis Detallado", "📋 Tabla CPM", "🧑‍💼 Asignación de Personal"])

    with tab1:
        st.subheader("Análisis y Recomendaciones")
        cb_nombre = linea_res.cuello_botella_info.get('nombre', 'N/A')
        st.info(f"**Cuello de Botella:** La estación **'{cb_nombre}'** es la más lenta, con un tiempo de **{linea_res.tiempo_ciclo_calculado:.2f} minutos**. Este es el factor que limita toda la producción.", icon="⚠️")
        
        if linea_res.eficiencia_linea < 70:
            st.warning("**Recomendación:** La eficiencia es baja. Considere redistribuir tareas de la estación cuello de botella a otras con más holgura. La capacitación cruzada (cross-training) del personal puede ser clave.", icon="🛠️")
        elif linea_res.eficiencia_linea < 85:
            st.success("**Oportunidad de Mejora:** La eficiencia es aceptable, pero hay margen para optimizar. Analice las tareas no críticas para ver si pueden absorber parte de la carga de trabajo de las estaciones críticas.", icon="👍")
        else:
            st.success("**¡Excelente Balance!** La línea opera con alta eficiencia. Mantenga el monitoreo para asegurar la sostenibilidad y busque mejoras incrementales.", icon="🏆")

    with tab2:
        st.subheader("Detalle de la Ruta Crítica (CPM)")
        cpm_data = [{"Estación": est.nombre, "Tiempo": est.tiempo, "ES": est.es, "EF": est.ef, "LS": est.ls, "LF": est.lf, "Holgura": est.holgura, "Crítica": "🔴 Sí" if est.es_critica else "🟢 No"} for est in linea_res.estaciones_lista]
        st.dataframe(cpm_data, use_container_width=True)

    with tab3:
        st.subheader("Asignación Sugerida de Empleados")
        st.dataframe(linea_res.empleados_asignados_por_estacion, use_container_width=True)
else:
    st.info("Configure los parámetros en la barra lateral y presione 'Calcular Balanceo' para ver los resultados.")

