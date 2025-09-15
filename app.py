# -*- coding: utf-8 -*-
"""
Aplicación Streamlit para el Balanceo de Líneas de Producción.

Versión 3.0: Interfaz rediseñada sin barra lateral, exportación a PDF profesional
con gráficos, y métricas de optimización avanzadas para un análisis más profundo.
"""
import streamlit as st
import datetime
import matplotlib
matplotlib.use('Agg') # Backend para entornos sin GUI
import matplotlib.pyplot as plt
from io import BytesIO

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

# --- Lógica de Negocio (Clases) ---
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
        # Inicialización de todas las métricas
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
        """Ejecuta toda la secuencia de cálculos."""
        self.calcular_cpm()
        self.calcular_metricas_avanzadas()
        self.asignar_empleados()

# --- Funciones de Generación (Gráficos, PDF, Twilio) ---
def generar_graficos(linea_obj):
    """Genera y devuelve los objetos de figura de Matplotlib para tiempos y empleados."""
    fig_pie, fig_bar = None, None
    if linea_obj.estaciones_lista and sum(e.tiempo for e in linea_obj.estaciones_lista) > 0:
        fig_pie, ax1 = plt.subplots(figsize=(5, 4))
        ax1.pie([e.tiempo for e in linea_obj.estaciones_lista], labels=[e.nombre for e in linea_obj.estaciones_lista], autopct='%1.1f%%', startangle=90)
        ax1.axis('equal')
        ax1.set_title('Distribución de Tiempos de Proceso')
        plt.tight_layout()
    if linea_obj.empleados_asignados_por_estacion:
        fig_bar, ax2 = plt.subplots(figsize=(5, 4))
        ax2.bar([a['nombre'] for a in linea_obj.empleados_asignados_por_estacion], [a['empleados'] for a in linea_obj.empleados_asignados_por_estacion], color='skyblue')
        ax2.set_xlabel('Estaciones')
        ax2.set_ylabel('Empleados Asignados')
        ax2.set_title('Asignación de Empleados Sugerida')
        plt.xticks(rotation=45, ha="right")
        plt.tight_layout()
    return fig_pie, fig_bar

def generar_reporte_pdf(linea_obj):
    """Crea un reporte PDF profesional con KPIs, tablas y gráficos."""
    if not IS_PDF_AVAILABLE:
        st.error("La librería 'reportlab' no está instalada. La exportación a PDF no está disponible.")
        return None
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=inch*0.5, leftMargin=inch*0.5, topMargin=inch*0.5, bottomMargin=inch*0.5)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("Reporte de Optimización de Línea de Producción", styles['h1']))
    story.append(Paragraph(f"Generado el: {datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')}", styles['Normal']))
    story.append(Spacer(1, 0.2*inch))

    # KPIs
    story.append(Paragraph("Indicadores Clave de Rendimiento (KPIs)", styles['h2']))
    kpi_data = [
        ["Eficiencia de Línea:", f"{linea_obj.eficiencia_linea:.2f}%"],
        ["Tiempo de Ciclo:", f"{linea_obj.tiempo_ciclo_calculado:.2f} min/ud"],
        ["Tasa de Producción:", f"{linea_obj.tasa_produccion:.2f} uds/hora"],
        ["Tiempo Total Estimado:", f"{linea_obj.tiempo_produccion_total_estimado:.2f} min"],
        ["Tiempo Inactivo Total (Holgura):", f"{linea_obj.tiempo_inactivo_total:.2f} min"]
    ]
    kpi_table = Table(kpi_data, colWidths=[3*inch, 2*inch])
    kpi_table.setStyle(TableStyle([('ALIGN', (0,0), (-1,-1), 'LEFT'), ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'), ('BOTTOMPADDING', (0,0), (-1,-1), 6)]))
    story.append(kpi_table)
    story.append(Spacer(1, 0.2*inch))

    # CPM Table
    story.append(Paragraph("Detalle de la Ruta Crítica (CPM)", styles['h2']))
    cpm_header = ["Estación", "Tiempo", "ES", "EF", "LS", "LF", "Holgura", "Crítica"]
    cpm_data = [cpm_header] + [[est.nombre, f"{est.tiempo:.2f}", f"{est.es:.2f}", f"{est.ef:.2f}", f"{est.ls:.2f}", f"{est.lf:.2f}", f"{est.holgura:.2f}", "Sí" if est.es_critica else "No"] for est in linea_obj.estaciones_lista]
    cpm_table = Table(cpm_data, hAlign='LEFT')
    cpm_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.grey), ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'), ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 12), ('GRID', (0,0), (-1,-1), 1, colors.black)
    ]))
    story.append(cpm_table)
    story.append(Spacer(1, 0.2*inch))

    # Graficos
    fig_pie, fig_bar = generar_graficos(linea_obj)
    charts = []
    if fig_pie:
        img_buffer = BytesIO()
        fig_pie.savefig(img_buffer, format='PNG', dpi=300)
        img_buffer.seek(0)
        charts.append(Image(img_buffer, width=3.5*inch, height=2.8*inch))
    if fig_bar:
        img_buffer = BytesIO()
        fig_bar.savefig(img_buffer, format='PNG', dpi=300)
        img_buffer.seek(0)
        charts.append(Image(img_buffer, width=3.5*inch, height=2.8*inch))
    if charts:
        story.append(Table([charts]))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

# --- Configuración Inicial y Estado ---
st.set_page_config(page_title="Optimizador de Líneas", layout="wide", page_icon="🏭")

DEFAULT_ESTACIONES = [
    {'nombre': 'Corte', 'tiempo': 2.0, 'predecesora': ''}, {'nombre': 'Doblado', 'tiempo': 3.0, 'predecesora': 'Corte'},
    {'nombre': 'Ensamblaje', 'tiempo': 5.0, 'predecesora': 'Doblado'}, {'nombre': 'Pintura', 'tiempo': 4.0, 'predecesora': 'Ensamblaje'},
    {'nombre': 'Empaque', 'tiempo': 1.5, 'predecesora': 'Pintura'}
]

if 'estaciones' not in st.session_state:
    st.session_state.estaciones = DEFAULT_ESTACIONES

# --- Interfaz de Usuario Principal ---
st.title("🏭 Optimizador Avanzado de Líneas de Producción")

with st.expander("⚙️ Configurar Simulación y Estaciones", expanded=True):
    col_params, col_actions = st.columns([1, 1])
    with col_params:
        st.subheader("Parámetros Globales")
        unidades = st.number_input("Unidades a Producir", min_value=1, value=100, step=10)
        empleados = st.number_input("Empleados Disponibles", min_value=1, value=5, step=1)
    
    with col_actions:
        st.subheader("Gestionar Estaciones")
        c1, c2 = st.columns(2)
        if c1.button("➕ Añadir Estación", use_container_width=True):
            st.session_state.estaciones.append({'nombre': '', 'tiempo': 1.0, 'predecesora': ''})
            st.rerun()
        if c2.button("➖ Quitar Última", use_container_width=True, disabled=len(st.session_state.estaciones) <= 1):
            st.session_state.estaciones.pop()
            st.rerun()

    st.markdown("---")
    st.subheader("Definición de Estaciones")
    
    # Layout dinámico de columnas para las estaciones
    cols = st.columns(max(1, min(len(st.session_state.estaciones), 4)))
    for i, est in enumerate(st.session_state.estaciones):
        with cols[i % 4]:
            st.markdown(f"**Estación {i+1}**")
            st.session_state.estaciones[i]['nombre'] = st.text_input("Nombre", value=est['nombre'], key=f"nombre_{i}")
            st.session_state.estaciones[i]['tiempo'] = st.number_input("Tiempo (min)", min_value=0.01, value=est['tiempo'], key=f"tiempo_{i}")
            predecesoras_opts = [""] + [e['nombre'] for j, e in enumerate(st.session_state.estaciones) if i != j and e['nombre']]
            current_pred = est['predecesora']
            try:
                idx = predecesoras_opts.index(current_pred)
            except ValueError:
                idx = 0
            st.session_state.estaciones[i]['predecesora'] = st.selectbox("Predecesora", options=predecesoras_opts, index=idx, key=f"pred_{i}")

# --- Botones de Acción ---
c1, c2, c3 = st.columns([2, 1, 1])
with c1:
    if st.button("🚀 Calcular y Optimizar", type="primary", use_container_width=True):
        with st.spinner("Realizando análisis completo..."):
            try:
                linea = LineaProduccion(st.session_state.estaciones, unidades, empleados)
                linea.ejecutar_calculos()
                st.session_state.results = {"linea_obj": linea}
                st.success("¡Análisis completado!")
            except Exception as e:
                st.error(f"Error en el cálculo: {e}")
                st.session_state.results = None

with c2:
    if 'results' in st.session_state and st.session_state.results:
        pdf_data = generar_reporte_pdf(st.session_state.results['linea_obj'])
        if pdf_data:
            st.download_button(label="📄 Descargar Reporte PDF", data=pdf_data, file_name="reporte_optimizacion.pdf", mime="application/pdf", use_container_width=True)

with c3:
    if st.button("🔄 Resetear", use_container_width=True):
        st.session_state.estaciones = DEFAULT_ESTACIONES
        st.session_state.results = None
        st.rerun()

# --- Panel de Resultados ---
if 'results' in st.session_state and st.session_state.results:
    linea_res = st.session_state.results['linea_obj']
    st.markdown("---")
    st.header("📊 Resultados de la Optimización")

    # KPIs
    kpi_cols = st.columns(5)
    kpi_cols[0].metric("Eficiencia", f"{linea_res.eficiencia_linea:.1f}%")
    kpi_cols[1].metric("Tiempo de Ciclo", f"{linea_res.tiempo_ciclo_calculado:.2f} min/ud")
    kpi_cols[2].metric("Tasa de Producción", f"{linea_res.tasa_produccion:.1f} uds/hr")
    kpi_cols[3].metric("Tiempo Total", f"{linea_res.tiempo_produccion_total_estimado:.1f} min")
    kpi_cols[4].metric("Tiempo Inactivo", f"{linea_res.tiempo_inactivo_total:.1f} min")

    # Tabs con análisis
    tab1, tab2, tab3 = st.tabs(["📈 **Análisis y Sugerencias**", "📋 **Tabla CPM**", "🧑‍💼 **Asignación de Personal**"])
    with tab1:
        st.subheader("Sugerencias de Optimización")
        cb_nombre = linea_res.cuello_botella_info.get('nombre', 'N/A')
        st.info(f"**Cuello de Botella:** La estación **'{cb_nombre}'** con **{linea_res.tiempo_ciclo_calculado:.2f} minutos** es el factor que limita toda la producción.", icon="⚠️")
        
        estaciones_con_holgura = sorted([est for est in linea_res.estaciones_lista if not est.es_critica and est.holgura > 0], key=lambda x: x.holgura, reverse=True)
        if linea_res.eficiencia_linea < 85 and estaciones_con_holgura:
            mejor_candidata = estaciones_con_holgura[0]
            st.warning(f"**Sugerencia Clave:** La eficiencia puede mejorar. Considere mover micro-tareas desde '{cb_nombre}' hacia la estación con más tiempo inactivo: **'{mejor_candidata.nombre}'**, que tiene **{mejor_candidata.holgura:.2f} minutos de holgura**.", icon="🛠️")
        elif linea_res.eficiencia_linea >= 85:
            st.success("**¡Excelente Balance!** La línea opera con alta eficiencia. El tiempo inactivo es mínimo. Mantenga el monitoreo para asegurar la sostenibilidad y busque mejoras incrementales.", icon="🏆")

    with tab2:
        st.dataframe(
            [{"Estación": est.nombre, "Tiempo": est.tiempo, "ES": est.es, "EF": est.ef, "LS": est.ls, "LF": est.lf, "Holgura": est.holgura, "Crítica": "🔴 Sí" if est.es_critica else "🟢 No"} for est in linea_res.estaciones_lista],
            use_container_width=True
        )

    with tab3:
        st.dataframe(linea_res.empleados_asignados_por_estacion, use_container_width=True)
else:
    st.info("Configure los parámetros y presione 'Calcular y Optimizar' para ver los resultados.")

