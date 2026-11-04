# 🛠️ BootScribe

Una alternativa ligera, portátil y de código abierto a herramientas comerciales de gestión UEFI, diseñada en Python y `tkinter`.

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

> **Flujo de trabajo recomendado:**
> 1. Crea y registra tu nueva entrada (ej. rEFInd) usando **BootScribe**.
> 2. Reinicia tu equipo e ingresa a la configuración de la **BIOS / UEFI**.
> 3. Habilita la nueva entrada creada y selecciónala como primera en el orden de prioridad del firmware (*Boot Priority Order*).

---

## Compilación a ejecutable (.exe)

Si deseas compilar la aplicación tú mismo a partir del código fuente:

1. Requisitos: Python 3.10+ e instalar PyInstaller:
   ```cmd
   pip install pyinstaller


Usar la app: Dentro de dist corre el archivo app_uefi.exe
