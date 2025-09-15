Herramienta de Optimización de Líneas de Producción con Streamlit
Esta es una aplicación web interactiva construida con Streamlit para analizar y optimizar líneas de producción. Permite a los usuarios definir estaciones de trabajo, sus tiempos y precedencias para calcular métricas clave, identificar cuellos de botella y visualizar los resultados.

Esta versión es una adaptación de una aplicación originalmente desarrollada con Flask.

Características
Configuración Dinámica: Define el número de estaciones, sus nombres, tiempos de proceso y predecesoras.

Cálculo de Ruta Crítica (CPM): Identifica automáticamente las estaciones críticas, la duración total del proyecto y las holguras de cada tarea.

Métricas de Eficiencia: Calcula la eficiencia de la línea, el tiempo de ciclo (Takt Time) y el tiempo total de producción estimado.

Identificación de Cuello de Botella: Señala la estación que limita el ritmo de producción.

Asignación de Empleados: Sugiere una distribución de los empleados disponibles basada en la carga de trabajo de cada estación.

Visualización de Datos: Genera gráficos de pastel y de barras para una fácil interpretación de la distribución de tiempos y la asignación de personal.

Exportación de Reportes: Permite descargar un resumen detallado de los resultados en formato de texto (.txt).

Cómo ejecutar la aplicación localmente
Clonar el repositorio:

git clone <URL-del-repositorio>
cd <nombre-del-repositorio>

Crear un entorno virtual (recomendado):

python -m venv venv
source venv/bin/activate  # En Windows: venv\Scripts\activate

Instalar las dependencias:
Asegúrate de tener todas las librerías necesarias listadas en requirements.txt.

pip install -r requirements.txt

Ejecutar la aplicación Streamlit:

streamlit run app.py

La aplicación se abrirá automáticamente en tu navegador web.

Despliegue en Streamlit Community Cloud
Esta aplicación está lista para ser desplegada gratuitamente en Streamlit Community Cloud.

Sube tu código a un repositorio público en GitHub. Asegúrate de que los archivos app.py y requirements.txt estén en la raíz del repositorio.

Ve a share.streamlit.io y regístrate o inicia sesión.

Haz clic en "New app" y conecta tu cuenta de GitHub.

Selecciona el repositorio, la rama y el archivo principal (app.py).

(Opcional) Configurar Secrets para Notificaciones: Si deseas habilitar las notificaciones por WhatsApp a través de Twilio, ve a la configuración avanzada (Advanced settings...) y añade tus credenciales como "Secrets". El formato debe ser el siguiente:

# secrets.toml
TWILIO_ACCOUNT_SID = "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
TWILIO_AUTH_TOKEN = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
TWILIO_WHATSAPP_FROM_NUMBER = "+14155238886" # Tu número de Twilio
DESTINATION_WHATSAPP_NUMBER = "+57xxxxxxxxxx" # Tu número de WhatsApp de destino

Haz clic en "Deploy!". Streamlit se encargará de instalar las dependencias y poner tu aplicación en línea.
