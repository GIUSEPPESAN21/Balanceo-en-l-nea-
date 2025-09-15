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
try:
    from twilio.rest import Client
    IS_TWILIO_AVAILABLE = True
except ImportError:
    IS_TWILIO_AVAILABLE = False
    Client = None # Definimos Client como None para evitar errores de NameError

# --- Lógica de Negocio (Clases Estacion y LineaProduccion) ---
# (Las clases se mantienen sin cambios)
class Estacion:
    """
    Representa una estación de trabajo en la línea de producción.
    """
    def __init__(self, nombre, tiempo, predecesora_nombre=""):
        self.nombre = nombre
        if not isinstance(tiempo, (int, float)) or tiempo <= 0:
            raise ValueError(f"El tiempo para la estación '{nombre}' debe ser un número positivo. Recibido: {tiempo}")
        self.tiempo = float(tiempo)
        self.predecesora_nombre = predecesora_nombre
        self.es, self.ef, self.ls, self.lf, self.holgura = 0.0, 0.0, 0.0, 0.0, 0.0
        self.es_critica = False

class LineaProduccion:
    """
    Gestiona el conjunto de estaciones y realiza todos los cálculos.
    """
    def __init__(self, estaciones_data, unidades_a_producir, num_empleados_disponibles):
        self.estaciones_dict = {}
        self.estaciones_lista = []
        self._procesar_estaciones_data(estaciones_data)
        self.unidades_a_producir = unidades_a_producir
        self.num_empleados_disponibles = num_empleados_disponibles
        self.tiempo_total_camino_critico = 0.0
        self.camino_critico_nombres = []
        self.tiempo_ciclo_calculado = 0.0
        self.tiempo_produccion_total_estimado = 0.0
        self.eficiencia_linea = 0.0
        self.cuello_botella_info = {}
        self.empleados_asignados_por_estacion = []

    def _procesar_estaciones_data(self, estaciones_data):
        nombres_vistos = set()
        for i, data in enumerate(estaciones_data):
            nombre = data.get("nombre")
            if not nombre: raise ValueError(f"La estación #{i+1} no tiene nombre.")
            if nombre.lower() in nombres_vistos: raise ValueError(f"Nombre de estación duplicado: '{nombre}'.")
            nombres_vistos.add(nombre.lower())
            self.estaciones_lista.append(Estacion(nombre, data.get("tiempo"), data.get("predecesora", "")))
            self.estaciones_dict[nombre] = self.estaciones_lista[-1]
        for est in self.estaciones_lista:
            if est.predecesora_nombre and est.predecesora_nombre not in self.estaciones_dict:
                raise ValueError(f"La predecesora '{est.predecesora_nombre}' para '{est.nombre}' no existe.")

    def calcular_cpm(self):
        # ... (Lógica de CPM sin cambios)
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
                self.camino_critico_nombres.append(est.nombre)
        if self.estaciones_lista:
            cuello_botella = max(self.estaciones_lista, key=lambda e: e.tiempo)
            self.cuello_botella_info = {"nombre": cuello_botella.nombre, "tiempo_proceso_individual": cuello_botella.tiempo}

    def calcular_metricas_produccion(self):
        # ... (Lógica de métricas sin cambios)
        tiempo_estacion_mas_larga = self.cuello_botella_info.get("tiempo_proceso_individual", 0)
        self.tiempo_ciclo_calculado = tiempo_estacion_mas_larga
        if self.unidades_a_producir > 0 and tiempo_estacion_mas_larga > 0:
            self.tiempo_produccion_total_estimado = self.tiempo_total_camino_critico + (self.unidades_a_producir - 1) * tiempo_estacion_mas_larga
        else:
            self.tiempo_produccion_total_estimado = self.tiempo_total_camino_critico
        sum_tiempos = sum(est.tiempo for est in self.estaciones_lista)
        if self.num_empleados_disponibles > 0 and tiempo_estacion_mas_larga > 0:
            self.eficiencia_linea = (sum_tiempos / (len(self.estaciones_lista) * tiempo_estacion_mas_larga)) * 100
        else:
            self.eficiencia_linea = 0.0

    def asignar_empleados(self):
        # ... (Lógica de asignación sin cambios)
        if not self.estaciones_lista or self.num_empleados_disponibles == 0: return
        total_tiempo_tareas = sum(est.tiempo for est in self.estaciones_lista)
        if total_tiempo_tareas == 0: return
        asignaciones = [{'nombre': e.nombre, 'ideal': e.tiempo / total_tiempo_tareas * self.num_empleados_disponibles} for e in self.estaciones_lista]
        for a in asignaciones: a['base'], a['fraccion'] = int(a['ideal']), a['ideal'] - int(a['ideal'])
        restantes = self.num_empleados_disponibles - sum(a['base'] for a in asignaciones)
        asignaciones.sort(key=lambda x: x['fraccion'], reverse=True)
        for i in range(restantes): asignaciones[i]['base'] += 1
        mapa_asignacion = {a['nombre']: a['base'] for a in asignaciones}
        self.empleados_asignados_por_estacion = [{"nombre": e.nombre, "empleados": mapa_asignacion.get(e.nombre, 0)} for e in self.estaciones_lista]

    def generar_texto_analisis_resultados(self):
        # ... (Lógica de texto sin cambios)
        return "..." # Implementación completa omitida por brevedad

# --- Funciones Auxiliares (sin cambios) ---
def generar_graficos(linea_obj):
    # ...
    return None, None
def generar_reporte_txt(results):
    # ...
    return b""

# --- Lógica de Twilio con Diagnóstico Mejorado ---
LOW_EFFICIENCY_THRESHOLD = 75

def inicializar_twilio_client():
    if not IS_TWILIO_AVAILABLE:
        st.session_state.twilio_configured = False
        return None

    # ===== NUEVO BLOQUE DE DIAGNÓSTICO =====
    if 'secrets_checked' not in st.session_state:
        if hasattr(st, 'secrets'):
            keys_found = st.secrets.keys()
            st.toast(f"Secrets encontrados: {list(keys_found)}", icon="🔍")
        else:
            st.toast("No se encontró el objeto st.secrets.", icon="❌")
        st.session_state.secrets_checked = True
    # ===== FIN DEL NUEVO BLOQUE =====

    try:
        required_secrets = ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM_NUMBER", "DESTINATION_WHATSAPP_NUMBER"]
        if hasattr(st, 'secrets') and all(key in st.secrets for key in required_secrets):
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
    if not st.session_state.get('twilio_configured', False) or st.session_state.twilio_client is None:
        return False
    try:
        from_number = st.secrets["TWILIO_WHATSAPP_FROM_NUMBER"]
        to_number = st.secrets["DESTINATION_WHATSAPP_NUMBER"]
        st.session_state.twilio_client.messages.create(
            from_=f'whatsapp:{from_number}',
            body=mensaje,
            to=f'whatsapp:{to_number}'
        )
        st.toast(f"¡Alerta de WhatsApp enviada a {to_number}!", icon="✅")
        return True
    except Exception as e:
        # ===== ERROR MÁS DETALLADO =====
        st.error(f"Error detallado al enviar WhatsApp: {e}")
        return False

# --- Interfaz de Streamlit (sin cambios funcionales mayores) ---
st.set_page_config(page_title="Optimización de Líneas", layout="wide", page_icon="⚙️")

if 'twilio_client' not in st.session_state:
    st.session_state.twilio_client = inicializar_twilio_client()

st.title("⚙️ Optimización de Líneas de Producción")

# ... (Resto de la UI sin cambios)
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

    current_len = len(st.session_state.estaciones)
    if num_estaciones > current_len:
        st.session_state.estaciones.extend([{'nombre': '', 'tiempo': 1.0, 'predecesora': ''}] * (num_estaciones - current_len))
    elif num_estaciones < current_len:
        st.session_state.estaciones = st.session_state.estaciones[:num_estaciones]
    
    for i in range(num_estaciones):
        with st.expander(f"Estación {i+1}: {st.session_state.estaciones[i]['nombre'] or 'Nueva'}", expanded=True):
            st.session_state.estaciones[i]['nombre'] = st.text_input(f"Nombre Estación {i+1}", value=st.session_state.estaciones[i]['nombre'], key=f"nombre_{i}")
            st.session_state.estaciones[i]['tiempo'] = st.number_input(f"Tiempo (min) {i+1}", min_value=0.01, value=st.session_state.estaciones[i]['tiempo'], key=f"tiempo_{i}")
            predecesoras_disponibles = [""] + [est['nombre'] for j, est in enumerate(st.session_state.estaciones) if i != j and est['nombre']]
            current_pred = st.session_state.estaciones[i]['predecesora']
            idx = predecesoras_disponibles.index(current_pred) if current_pred in predecesoras_disponibles else 0
            st.session_state.estaciones[i]['predecesora'] = st.selectbox(f"Predecesora {i+1}", options=predecesoras_disponibles, index=idx, key=f"pred_{i}")

if st.sidebar.button("Calcular Balanceo", type="primary", use_container_width=True):
    # ... (Lógica del botón sin cambios)
    pass

# ... (Renderizado de resultados sin cambios)

