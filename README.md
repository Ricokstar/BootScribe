# BootScribe v3.0.0

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011-0078D6?logo=windows&logoColor=white)
![License](https://img.shields.io/badge/License-Open%20Source-green)
![Language](https://img.shields.io/badge/Language-EN%20%7C%20ES-orange)

> 🇺🇸 **English** | 🇪🇸 *[Versión en Español más abajo](#-versión-en-español)*

A lightweight, portable, and open-source alternative to commercial UEFI management tools, developed in Python and `tkinter`.

This utility allows you to list, create, reorder, and configure UEFI boot entries (such as **rEFInd**, **GRUB**, or other Linux environments), as well as redirect or toggle the native Windows boot manager (`{bootmgr}`) directly in NVRAM using advanced `bcdedit` commands.

---

## ✨ Highlights & Key Features

- **Multi-language Support (ES / EN):** Bilingual interface with dynamic switching between **English** and **Spanish**.
- **Preview & Safe Confirmation:** Before applying critical modifications to NVRAM or `{bootmgr}`, BootScribe displays an exact preview of the commands and changes about to be executed, requiring explicit user confirmation.
- **NVRAM/UEFI Entry Listing:** Displays names, GUID identifiers, and full paths to `.efi` executables.
- **Universal Bootmgr Toggle:** Allows associating or releasing the path of the native Windows boot manager (`{bootmgr}`) to any custom bootloader (rEFInd, GRUB, etc.) following the official rEFInd documentation method.
- **Entry Injection:** Easily adds `.efi` executables from any accessible EFI System Partition (ESP).
- **Priority Management:** Modifies the global boot order (`fwbootmgr`) directly from Windows.
- **Direct Reboot to UEFI:** One-click option to reboot the machine directly into the BIOS/UEFI firmware interface (`shutdown /r /fw`).
- **Portable & Dependency-free:** Distributed as a ready-to-use executable package for Windows.

---

## ⚙️ How It Works (`{bootmgr}`)

On modern Windows systems with NVRAM and Secure Boot restrictions, the operating system often reverts boot order changes after an update. To solve this, BootScribe uses path redirection in `{bootmgr}`:

1. **Active Redirection:** Executes `bcdedit /set "{bootmgr}" path <EFI_PATH>` to force the primary loader to boot your preferred manager (e.g., `\EFI\refind\refind_x64.efi` or a GRUB binary).
2. **Restoration / Release:** Allows reverting the path back to the default Windows executable (`\EFI\Microsoft\Boot\bootmgfw.efi`) or switching to another entry without rigid configurations.

---

## 🚀 How to Use BootScribe

1. **Download & Run:** Go to the [Releases](../../releases) section on GitHub, download the `.zip` file of the latest version, extract it, and run **`BootScribe.exe`** (it will automatically request Administrator privileges, which are required to interact with `bcdedit`).
2. **Select Language:** Choose between English or Spanish in the interface according to your preference.
3. **Manage Entries:** Review the list of UEFI entries detected in your NVRAM along with their GUIDs and paths.
4. **Universal Toggle or Editing:** Select your desired `.efi` executable (rEFInd, GRUB, etc.) and choose the action to perform.
5. **Review & Confirm:** Check the proposed changes in the confirmation window and accept them to write them to the system.

---

## ⚠️ Important Notes

> [!IMPORTANT]
> **Regarding Disabled Entries / rEFInd**  
> On modern Windows 10 and 11 systems, operating system security policies may prevent modifying the active state (*Active Flag*) of third-party UEFI entries directly from Windows.
>
> **Recommended workflow for new entries:**
> 1. Create and register your new entry (e.g., rEFInd) using **BootScribe**.
> 2. Use the direct reboot button to enter the **BIOS / UEFI** settings.
> 3. Enable the newly created entry and set it as first in the firmware priority order (*Boot Priority Order*).

> [!WARNING]
> **Regarding Windows Defender / SmartScreen**  
> Because BootScribe is a free, open-source tool compiled without a commercial digital signing certificate, Windows SmartScreen may show a warning when opening it for the first time. You can safely bypass it by clicking **"More info" → "Run anyway"**. All source code is fully available in this repository for auditing.

---

## 🛠️ Requirements & Building from Source

If you want to clone this repository and build the application yourself:

1. **Requirements:** Install **Python 3.10+** and **PyInstaller**:
   ```cmd
   pip install pyinstaller
   ```

2. **Build the Application:** Run the following command in the project root (making sure to include the `fonts` folder and `bootscribe.ico` icon):
   ```cmd
   pyinstaller --clean --onedir --noconsole --uac-admin --icon bootscribe.ico --add-data "fonts;fonts" --name BootScribe bootscribe.py
   ```

3. **Run:** You will find the compiled program ready to use inside the `dist\BootScribe\BootScribe.exe` folder.

---
---

# 🇪🇸 Versión en Español

## BootScribe v3.0.0

Una alternativa ligera, portátil y de código abierto a las herramientas comerciales de gestión UEFI, desarrollada en Python y `tkinter`.

Esta utilidad permite listar, crear, reordenar y configurar entradas de arranque UEFI (como **rEFInd**, **GRUB** u otros entornos Linux), así como redirigir o conmutar el gestor de arranque nativo de Windows (`{bootmgr}`) directamente en la NVRAM mediante comandos avanzados de `bcdedit`.

---

## ✨ Novedades y Características Principales

- **Soporte Multi-idioma (ES / EN):** Interfaz bilingüe con cambio dinámico entre **Español** e **Inglés**.
- **Vista Previa y Confirmación Segura:** Antes de aplicar modificaciones críticas en la NVRAM o en `{bootmgr}`, BootScribe muestra una vista previa exacta de los comandos y cambios que se van a ejecutar, requiriendo confirmación explícita del usuario.
- **Listado de entradas NVRAM/UEFI:** Muestra nombres, identificadores GUID y rutas completas a ejecutables `.efi`.
- **Conmutación Universal de Bootmgr (Toggle):** Permite asociar o liberar la ruta del gestor nativo de Windows (`{bootmgr}`) hacia cualquier cargador de arranque personalizado (rEFInd, GRUB, etc.) siguiendo el método recomendado por la documentación de rEFInd.
- **Inyección de nuevas entradas:** Añade ejecutables `.efi` desde cualquier partición de sistema (ESP) accesible.
- **Gestión de prioridades:** Modifica el orden de arranque global (`fwbootmgr`) directamente desde Windows.
- **Reinicio directo a UEFI:** Un clic para reiniciar el equipo directamente en la interfaz del firmware BIOS/UEFI (`shutdown /r /fw`).
- **Portátil y sin dependencias:** Distribuido como paquete ejecutable listo para usar en Windows.

---

## ⚙️ Método de Funcionamiento (`{bootmgr}`)

En sistemas Windows modernos con restricciones de NVRAM y Secure Boot, el sistema operativo suele revertir los cambios en el orden de arranque tras una actualización. Para solucionarlo, BootScribe emplea la técnica de redirección de ruta en `{bootmgr}`:

1. **Redirección activa:** Ejecuta `bcdedit /set "{bootmgr}" path <RUTA_EFI>` para indicar al cargador principal que inicie el gestor de preferencia (por ejemplo, `\EFI\refind\refind_x64.efi` o un binario de GRUB).
2. **Restauración / Liberación:** Permite revertir la ruta al ejecutable predeterminado de Windows (`\EFI\Microsoft\Boot\bootmgfw.efi`) o alternar hacia otra entrada sin quedar atado a configuraciones rígidas.

---

## 🚀 Cómo usar BootScribe

1. **Descargar y Ejecutar:** Ve a la sección de [Releases](../../releases) en GitHub, descarga el archivo `.zip` de la última versión, extráelo y ejecuta **`BootScribe.exe`** (solicitará permisos de Administrador automáticamente, necesarios para interactuar con `bcdedit`).
2. **Seleccionar Idioma:** Elige entre Español o Inglés según tu preferencia en la interfaz.
3. **Gestionar Entradas:** Revisa la lista de entradas UEFI detectadas en tu NVRAM con sus respectivos GUID y rutas.
4. **Conmutación Universal (Toggle) o Edición:** Selecciona el ejecutable `.efi` deseado (rEFInd, GRUB, etc.) y elige la acción a realizar.
5. **Revisar y Confirmar:** Verifica los cambios propuestos en la ventana de confirmación y acéptalos para escribirlos en el sistema.

---

## ⚠️ Notas Importantes

> [!IMPORTANT]
> **Sobre Entradas Deshabilitadas / rEFInd**  
> En sistemas modernos con Windows 10 y 11, las políticas de seguridad del sistema operativo pueden impedir modificar el estado de habilitación (*Active Flag*) de una entrada UEFI creada por terceros directamente desde Windows.
>
> **Flujo de trabajo recomendado para nuevas entradas:**
> 1. Crea y registra tu nueva entrada (ej. rEFInd) usando **BootScribe**.
> 2. Utiliza el botón de reinicio directo para ingresar a la configuración de la **BIOS / UEFI**.
> 3. Habilita la nueva entrada creada y selecciónala como primera en el orden de prioridad del firmware (*Boot Priority Order*).

> [!WARNING]
> **Sobre Windows Defender / SmartScreen**  
> Como BootScribe es una herramienta gratuita y de código abierto compilada sin un certificado comercial de firma digital, es posible que Windows SmartScreen muestre una advertencia al abrirlo por primera vez. Puedes omitirla de forma segura haciendo clic en **"Más información" → "Ejecutar de todos modos"**. Todo el código fuente está disponible en este repositorio para su auditoría.

---

## 🛠️ Requisitos y Compilación desde el Código Fuente

Si deseas clonar este repositorio y compilar la aplicación tú mismo:

1. **Requisitos:** Instalar **Python 3.10** o superior e instalar **PyInstaller**:
   ```cmd
   pip install pyinstaller
   ```

2. **Compilar la aplicación:** Ejecuta el siguiente comando en la raíz del proyecto (asegurándote de incluir la carpeta `fonts` y el icono `bootscribe.ico`):
   ```cmd
   pyinstaller --clean --onedir --noconsole --uac-admin --icon bootscribe.ico --add-data "fonts;fonts" --name BootScribe bootscribe.py
   ```

3. **Ejecutar:** Encontrarás el programa compilado y listo para usar dentro de la carpeta `dist\BootScribe\BootScribe.exe`.