# BootScribe v2.0.0
<img width="1577" height="912" alt="Vista BootScribe v1 5" src="https://github.com/user-attachments/assets/cd9898d2-3a16-4ca1-9a3e-b39a6d8feae8" />
Una alternativa ligera, portátil y de código abierto a herramientas comerciales de gestión UEFI, desarrollada en Python y tkinter.

Esta utilidad permite listar, crear, reordenar y configurar entradas de arranque UEFI (como rEFInd, GRUB u otros entornos), así como redirigir o conmutar el gestor de arranque nativo de Windows ({bootmgr}) de forma dinámica directamente en la NVRAM mediante comandos avanzados de bcdedit.

---

## Características Principales

- Listado de entradas NVRAM: Muestra nombres, identificadores GUID y rutas completas a ejecutables .efi.
- Conmutación Universal de Bootmgr (Toggle): Permite asociar o liberar la ruta del gestor nativo de Windows ({bootmgr}) hacia cualquier cargador de arranque personalizado (rEFInd, GRUB, etc.) utilizando el método oficial recomendado por la documentación de rEFInd.
- Inyección de nuevas entradas: Permite añadir ejecutables .efi desde cualquier partición de sistema (ESP) accesible.
- Orden de arranque: Modifica las prioridades del menú global de la NVRAM de forma directa.
- Reinicio a UEFI: Opción directa para reiniciar la máquina directamente en la interfaz del firmware/BIOS (shutdown /r /fw).
- Portátil y modular: Diseñado para compilarse en un ejecutable único sin dependencias externas en el entorno final.

---

## Método de Funcionamiento ({bootmgr})

En sistemas Windows modernos con restricciones de NVRAM y Secure Boot, modificar el orden de arranque a veces es revertido por el sistema operativo tras una actualización. 

Para solucionar esto, BootScribe utiliza la técnica de redirección de ruta en {bootmgr}:

1. Redirección activa: Ejecuta los comandos bcdedit /set "{bootmgr}" path <RUTA_EFI> para forzar al cargador principal a iniciar el gestor de preferencia (por ejemplo, \EFI\refind\refind_x64.efi o un binario de GRUB).
2. Restauración / Liberación: Permite revertir la ruta al ejecutable predeterminado de Windows (\EFI\Microsoft\Boot\bootmgfw.efi) o alternar hacia otra entrada sin quedar restringido a configuraciones rígidas.

---

## Requisitos y Compilación

Para clonar este repositorio y generar el ejecutable portable (.exe) a partir del archivo ejecutable principal (app_uefi.py):

1. Instalar Python 3.10 o superior.
2. Instalar PyInstaller ejecutando en la consola:
   pip install pyinstaller

3. Compilar la aplicación en un solo archivo ejecutable:
   pyinstaller --noconsole --onefile app_uefi.py

El ejecutable resultante se ubicará en la carpeta dist/ dentro del directorio del proyecto. Se recomienda ejecutar la aplicación siempre con privilegios de Administrador para permitir la interacción con bcdedit.

---

Permite crear, listar, reordenar y preparar entradas de arranque UEFI (como **rEFInd**, **GRUB** o entornos Linux) directamente en el firmware de la placa madre a través de comandos avanzados de `bcdedit` en Windows.

---

## Características
- **Listado de entradas NVRAM/UEFI:** Muestra nombres, GUIDs y rutas de ejecutables `.efi`.
- **Inyección de nuevas entradas:** Agrega fácilmente gestores de arranque externos (ej. `\EFI\refind\refind_x64.efi`).
- **Gestión de prioridades:** Modifica el orden de arranque (`fwbootmgr`) desde Windows.
- **Reinicio directo a UEFI:** Un clic para reiniciar el equipo directo a la pantalla de configuración de la BIOS/UEFI.
- **100% Portátil:** Compilable a un único archivo `.exe` sin dependencias externas.

---

## ⚠️ Nota Importante sobre Entradas Deshabilitadas / rEFInd

En sistemas modernos con Windows 10 y 11, las políticas de seguridad del sistema operativo a veces impiden modificar el estado de habilitación (*Active Flag*) de una entrada UEFI creada por terceros directamente desde la API de Windows.
Pero ayuda a crear entradas nuevas de EFI

> **Flujo de trabajo recomendado:**
> 1. Crea y registra tu nueva entrada (ej. rEFInd) usando **BootScribe**.
> 2. Reinicia tu equipo e ingresa a la configuración de la **BIOS / UEFI**.
> 3. Habilita la nueva entrada creada y selecciónala como primera en el orden de prioridad del firmware (*Boot Priority Order*).

## Cómo usar BootScribe
Descargar y Ejecutar: Ve a la sección de Releases en GitHub, descarga el archivo BootScribe.exe y ejecútalo siempre como Administrador (necesario para modificar los registros del sistema de arranque).

Gestionar Entradas: La interfaz listará automáticamente todas las entradas UEFI detectadas en tu NVRAM con sus respectivos identificadores y rutas.

Conmutación Universal (Toggle): Selecciona en la tabla el ejecutable .efi que prefieras (rEFInd, GRUB, etc.) y haz clic en el botón principal para redirigir el gestor de arranque del sistema ({bootmgr}) de forma dinámica. Vuelve a presionarlo cuando desees liberar o alternar la ruta.

Herramientas Adicionales: Puedes añadir nuevas entradas personalizadas, cambiar prioridades de arranque o utilizar el botón de reinicio directo al firmware UEFI con un solo clic.

## ⚠️⚠️ Nota sobre Windows Defender / SmartScreen:

Como BootScribe es una herramienta gratuita y de código abierto compilada en un único ejecutable sin un costoso certificado de firma digital, es posible que Windows SmartScreen muestre una advertencia al ejecutarlo. Puedes omitirla de forma segura haciendo clic en "Más información" -> "Ejecutar de todos modos". Todo el código fuente está disponible en este repositorio para su total revisión.

## Compilación a ejecutable (.exe)

Si deseas compilar la aplicación tú mismo a partir del código fuente:

1. Requisitos: Python 3.10+ e instalar PyInstaller:
   ```cmd
   pip install pyinstaller


Usar la app: Dentro de dist corre el archivo BootScribe.exe
