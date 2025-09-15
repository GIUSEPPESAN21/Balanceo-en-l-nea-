# -*- coding: utf-8 -*-
"""
Aplicación Streamlit para el Balanceo de Líneas de Producción.

Versión 5.0: Se reestructura la interfaz principal en pestañas.
La funcionalidad del optimizador se mueve a la pestaña "Optimizador" y
la información del autor a una nueva pestaña "Acerca de".
"""
import streamlit as st
import datetime
import matplotlib
matplotlib.use('Agg') # Backend para entornos sin GUI
import matplotlib.pyplot as plt
from io import BytesIO
import random

# --- Importaciones para PDF y Twilio ---
try:
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    IS_PDF_AVAILABLE = True
except ImportError:
    IS_PDF_AVAILABLE = False

try:
    from twilio.rest import Client
    from twilio.base.exceptions import TwilioRestException
    IS_TWILIO_AVAILABLE = True
except ImportError:
    IS_TWILIO_AVAILABLE = False
    Client, TwilioRestException = None, None

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
    """Gestiona los cálculos de la línea de producción con métricas avanzadas."""
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
        self.tasa_produccion = 0.0
        self.tiempo_inactivo_total = 0.0

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
        self.camino_critico_nombres = sorted([est.nombre for est in self.estaciones_lista if est.es_critica])
        if self.estaciones_lista:
            cuello_botella = max(self.estaciones_lista, key=lambda e: e.tiempo)
            self.cuello_botella_info = {"nombre": cuello_botella.nombre, "tiempo_proceso_individual": cuello_botella.tiempo}

    def calcular_metricas_avanzadas(self):
        tiempo_cuello_botella = self.cuello_botella_info.get("tiempo_proceso_individual", 0)
        self.tiempo_ciclo_calculado = tiempo_cuello_botella
        if self.unidades_a_producir > 0 and tiempo_cuello_botella > 0:
            self.tiempo_produccion_total_estimado = self.tiempo_total_camino_critico + (self.unidades_a_producir - 1) * tiempo_cuello_botella
            self.tasa_produccion = 60 / tiempo_cuello_botella # Unidades por hora
        else:
            self.tiempo_produccion_total_estimado = self.tiempo_total_camino_critico
            self.tasa_produccion = 0.0
        
        sum_tiempos = sum(est.tiempo for est in self.estaciones_lista)
        denominador = len(self.estaciones_lista) * tiempo_cuello_botella
        self.eficiencia_linea = (sum_tiempos / denominador) * 100 if denominador > 0 else 0.0
        self.tiempo_inactivo_total = sum(est.holgura for est in self.estaciones_lista if not est.es_critica)

    def asignar_empleados(self):
        total_tiempo = sum(est.tiempo for est in self.estaciones_lista)
        if total_tiempo == 0 or self.num_empleados_disponibles == 0:
            self.empleados_asignados_por_estacion = [{"nombre": e.nombre, "empleados": 0} for e in self.estaciones_lista]
            return
        asignaciones = [{'nombre': e.nombre, 'ideal': e.tiempo / total_tiempo * self.num_empleados_disponibles} for e in self.estaciones_lista]
        for a in asignaciones: a['base'], a['fraccion'] = int(a['ideal']), a['ideal'] - int(a['ideal'])
        restantes = self.num_empleados_disponibles - sum(a['base'] for a in asignaciones)
        asignaciones.sort(key=lambda x: x['fraccion'], reverse=True)
        for i in range(min(restantes, len(asignaciones))): asignaciones[i]['base'] += 1
        mapa = {a['nombre']: a['base'] for a in asignaciones}
        self.empleados_asignados_por_estacion = [{"nombre": e.nombre, "empleados": mapa.get(e.nombre, 0)} for e in self.estaciones_lista]
    
    def ejecutar_calculos(self):
        self.calcular_cpm()
        self.calcular_metricas_avanzadas()
        self.asignar_empleados()

# --- Lógica de Twilio Reintegrada ---
LOW_EFFICIENCY_THRESHOLD = 85

def inicializar_twilio_client():
    if not IS_TWILIO_AVAILABLE: return None
    try:
        if hasattr(st, 'secrets') and all(k in st.secrets for k in ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"]):
            account_sid = st.secrets["TWILIO_ACCOUNT_SID"]
            auth_token = st.secrets["TWILIO_AUTH_TOKEN"]
            if account_sid.startswith("AC") and len(auth_token) > 30:
                st.session_state.twilio_configured = True
                return Client(account_sid, auth_token)
    except Exception:
        pass
    st.session_state.twilio_configured = False
    return None

def enviar_alerta_whatsapp(mensaje):
    if 'twilio_client' not in st.session_state or not st.session_state.twilio_client:
        return
    if not st.session_state.get('twilio_configured'):
        st.warning("Credenciales de Twilio no configuradas.", icon="⚠️")
        return
    try:
        from_number = st.secrets["TWILIO_WHATSAPP_FROM_NUMBER"]
        to_number = st.secrets["DESTINATION_WHATSAPP_NUMBER"]
        codigo_aleatorio = random.randint(100000, 999999)
        mensaje_final = f"Your Twilio code is {codigo_aleatorio}\n\n{mensaje}"
        st.session_state.twilio_client.messages.create(
            from_=f'whatsapp:{from_number}', body=mensaje_final, to=f'whatsapp:{to_number}'
        )
        st.toast(f"¡Alerta enviada a {to_number}!", icon="✅")
    except TwilioRestException as e:
        st.error(f"Error de Twilio: {e.msg}", icon="🚨")
        if e.code == 21608: st.warning("Reactiva tu Sandbox de WhatsApp.", icon="📱")
    except Exception as e:
        st.error(f"Error inesperado al enviar WhatsApp: {e}", icon="🚨")

# --- Funciones de Generación (Gráficos, PDF) ---
def generar_reporte_pdf(linea_obj):
    if not IS_PDF_AVAILABLE: return None
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=inch*0.5, bottomMargin=inch*0.5)
    styles = getSampleStyleSheet()
    story = []
    story.append(Paragraph("Reporte de Optimización de Línea", styles['h1']))
    story.append(Spacer(1, 0.2*inch))
    kpi_data = [
        ["Eficiencia de Línea:", f"{linea_obj.eficiencia_linea:.2f}%"], ["Tiempo de Ciclo:", f"{linea_obj.tiempo_ciclo_calculado:.2f} min/ud"],
        ["Tasa de Producción:", f"{linea_obj.tasa_produccion:.2f} uds/hora"], ["Tiempo Inactivo Total:", f"{linea_obj.tiempo_inactivo_total:.2f} min"]
    ]
    story.append(Table(kpi_data, style=[('ALIGN', (0,0), (-1,-1), 'LEFT'), ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold')]))
    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph("Detalle de la Ruta Crítica (CPM)", styles['h2']))
    cpm_header = ["Estación", "Tiempo", "ES", "EF", "LS", "LF", "Holgura", "Crítica"]
    cpm_data = [cpm_header] + [[est.nombre, f"{est.tiempo:.2f}", f"{est.es:.2f}", f"{est.ef:.2f}", f"{est.ls:.2f}", f"{est.lf:.2f}", f"{est.holgura:.2f}", "Sí" if est.es_critica else "No"] for est in linea_obj.estaciones_lista]
    story.append(Table(cpm_data, style=[('BACKGROUND', (0,0), (-1,0), colors.grey), ('GRID', (0,0), (-1,-1), 1, colors.black)]))
    fig_pie, ax1 = plt.subplots(); ax1.pie([e.tiempo for e in linea_obj.estaciones_lista], labels=[e.nombre for e in linea_obj.estaciones_lista], autopct='%1.1f%%'); ax1.axis('equal'); ax1.set_title('Tiempos')
    fig_bar, ax2 = plt.subplots(); ax2.bar([a['nombre'] for a in linea_obj.empleados_asignados_por_estacion], [a['empleados'] for a in linea_obj.empleados_asignados_por_estacion]); ax2.set_title('Empleados'); plt.xticks(rotation=45)
    charts = []
    for fig in [fig_pie, fig_bar]:
        buf = BytesIO(); fig.savefig(buf, format='PNG'); buf.seek(0)
        charts.append(Image(buf, width=3.5*inch, height=2.8*inch))
    story.append(Table([charts])); doc.build(story); buffer.seek(0)
    return buffer.getvalue()

# --- Configuración Inicial y Estado ---
st.set_page_config(page_title="Optimizador de Líneas", layout="wide", page_icon="🏭")
if 'estaciones' not in st.session_state:
    st.session_state.estaciones = [
        {'nombre': 'Corte', 'tiempo': 2.0, 'predecesora': ''}, {'nombre': 'Doblado', 'tiempo': 3.0, 'predecesora': 'Corte'},
        {'nombre': 'Ensamblaje', 'tiempo': 5.0, 'predecesora': 'Doblado'}, {'nombre': 'Pintura', 'tiempo': 4.0, 'predecesora': 'Ensamblaje'},
        {'nombre': 'Empaque', 'tiempo': 1.5, 'predecesora': 'Pintura'}
    ]
if 'twilio_client' not in st.session_state:
    st.session_state.twilio_client = inicializar_twilio_client()

# --- INTERFAZ PRINCIPAL CON PESTAÑAS ---
st.title("🏭 Optimizador Avanzado de Líneas de Producción")
tab_optimizador, tab_acerca_de = st.tabs(["🚀 Optimizador", "ℹ️ Acerca de"])

# --- Pestaña del Optimizador ---
with tab_optimizador:
    with st.expander("⚙️ **Haga clic para configurar la simulación y las estaciones**", expanded=True):
        col_params, col_actions = st.columns([1, 1])
        with col_params:
            unidades = st.number_input("Unidades a Producir", 1, value=100, step=10)
            empleados = st.number_input("Empleados Disponibles", 1, value=5, step=1)
        with col_actions:
            st.subheader("Gestionar Estaciones")
            c1, c2 = st.columns(2)
            if c1.button("➕ Añadir Estación", use_container_width=True, key="add_station"):
                st.session_state.estaciones.append({'nombre': '', 'tiempo': 1.0, 'predecesora': ''})
                st.rerun()
            if c2.button("➖ Quitar Última", use_container_width=True, disabled=len(st.session_state.estaciones) <= 1, key="remove_station"):
                st.session_state.estaciones.pop()
                st.rerun()

        st.subheader("Definición de Estaciones")
        cols = st.columns(max(1, len(st.session_state.estaciones)))
        for i, est in enumerate(st.session_state.estaciones):
            with cols[i % len(cols)]:
                st.markdown(f"**Estación {i+1}**")
                st.session_state.estaciones[i]['nombre'] = st.text_input("Nombre", est['nombre'], key=f"nombre_{i}")
                st.session_state.estaciones[i]['tiempo'] = st.number_input("Tiempo (min)", 0.01, value=est['tiempo'], key=f"tiempo_{i}")
                opts = [""] + [e['nombre'] for j, e in enumerate(st.session_state.estaciones) if i != j and e['nombre']]
                st.session_state.estaciones[i]['predecesora'] = st.selectbox("Predecesora", opts, index=(opts.index(est['predecesora']) if est['predecesora'] in opts else 0), key=f"pred_{i}")

    c1, c2, c3 = st.columns([2, 1, 1])
    if c1.button("🚀 Calcular y Optimizar", type="primary", use_container_width=True, key="calculate"):
        try:
            linea = LineaProduccion(st.session_state.estaciones, unidades, empleados)
            linea.ejecutar_calculos()
            st.session_state.results = {"linea_obj": linea}
            st.success("¡Análisis completado!")
            if linea.eficiencia_linea < LOW_EFFICIENCY_THRESHOLD:
                mensaje = f"¡Alerta! Eficiencia baja: {linea.eficiencia_linea:.1f}%. Cuello de botella: '{linea.cuello_botella_info.get('nombre', 'N/A')}'."
                enviar_alerta_whatsapp(mensaje)
        except Exception as e:
            st.error(f"Error en el cálculo: {e}")
            st.session_state.results = None

    if 'results' in st.session_state and st.session_state.results:
        if c2.download_button("📄 Descargar PDF", generar_reporte_pdf(st.session_state.results['linea_obj']), "reporte_balanceo.pdf", "application/pdf", use_container_width=True, key="download"):
            pass

    if c3.button("🔄 Resetear", use_container_width=True, key="reset"):
        st.session_state.results = None
        st.session_state.estaciones = [
            {'nombre': 'Corte', 'tiempo': 2.0, 'predecesora': ''}, {'nombre': 'Doblado', 'tiempo': 3.0, 'predecesora': 'Corte'},
            {'nombre': 'Ensamblaje', 'tiempo': 5.0, 'predecesora': 'Doblado'}, {'nombre': 'Pintura', 'tiempo': 4.0, 'predecesora': 'Ensamblaje'},
            {'nombre': 'Empaque', 'tiempo': 1.5, 'predecesora': 'Pintura'}
        ]
        st.rerun()

    if 'results' in st.session_state and st.session_state.results:
        linea_res = st.session_state.results['linea_obj']
        st.markdown("---")
        st.header("📊 Resultados de la Optimización")
        kpi_cols = st.columns(5)
        kpi_cols[0].metric("Eficiencia", f"{linea_res.eficiencia_linea:.1f}%")
        kpi_cols[1].metric("Tiempo de Ciclo", f"{linea_res.tiempo_ciclo_calculado:.2f} min/ud")
        kpi_cols[2].metric("Tasa de Producción", f"{linea_res.tasa_produccion:.1f} uds/hr")
        kpi_cols[3].metric("Tiempo Total", f"{linea_res.tiempo_produccion_total_estimado:.1f} min")
        kpi_cols[4].metric("Tiempo Inactivo", f"{linea_res.tiempo_inactivo_total:.1f} min")

        res_tab1, res_tab2, res_tab3 = st.tabs(["📈 **Análisis y Sugerencias**", "📋 **Tabla CPM**", "🧑‍💼 **Asignación de Personal**"])
        with res_tab1:
            cb_nombre = linea_res.cuello_botella_info.get('nombre', 'N/A')
            st.info(f"**Cuello de Botella:** Estación **'{cb_nombre}'** ({linea_res.tiempo_ciclo_calculado:.2f} min).", icon="⚠️")
            candidatas = sorted([est for est in linea_res.estaciones_lista if not est.es_critica], key=lambda x: x.holgura, reverse=True)
            if linea_res.eficiencia_linea < 85 and candidatas:
                st.warning(f"**Sugerencia:** Redistribuir tareas desde '{cb_nombre}' hacia **'{candidatas[0].nombre}'** (holgura de {candidatas[0].holgura:.2f} min).", icon="🛠️")
        with res_tab2:
            st.dataframe([{"Estación": est.nombre, "Tiempo": est.tiempo, "ES": est.es, "EF": est.ef, "LS": est.ls, "LF": est.lf, "Holgura": f"{est.holgura:.2f}", "Crítica": "🔴 Sí" if est.es_critica else "🟢 No"} for est in linea_res.estaciones_lista])
        with res_tab3:
            st.dataframe(linea_res.empleados_asignados_por_estacion)

# --- Pestaña de "Acerca de" ---
with tab_acerca_de:
    st.header("Autor del Software")
    st.write("---")
    col1, col2 = st.columns([1, 2])
    with col1:
        # Placeholder for an image, you can replace the URL with a direct link to a photo
        st.image("https://placehold.co/200x200/2B3137/FFFFFF?text=J.S.", width=200, caption="Joseph Javier Sánchez Acuña")
    with col2:
        st.subheader("Joseph Javier Sánchez Acuña")
        st.write("**Ingeniero Industrial, Experto en Inteligencia Artificial y Desarrollo de Software.**")
        st.markdown("""
        Esta aplicación fue desarrollada como una herramienta avanzada para el análisis y balanceo de líneas de producción.
        Combina principios de ingeniería industrial con desarrollo de software moderno para proporcionar una solución
        intuitiva y potente.
        """)
    st.write("---")
    st.subheader("Contacto y Enlaces")
    st.write(
        "🔗 [**LinkedIn**](https://www.linkedin.com/in/joseph-javier-sánchez-acuña-150410275) &nbsp;&nbsp; | &nbsp;&nbsp;"
        "📂 [**GitHub**](https://github.com/GIUSEPPESAN21) &nbsp;&nbsp; | &nbsp;&nbsp;"
        "📧 **joseph.sanchez@uniminuto.edu.co**"
    )

