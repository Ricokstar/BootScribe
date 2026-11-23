# 🇺🇸 English | 🇪🇸 _[Español](#-español)_

# BootScribe v3.1.0

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011-0078D6?logo=windows&logoColor=white)
![License](https://img.shields.io/badge/License-Open%20Source-green)
![Language](https://img.shields.io/badge/Language-EN%20%7C%20ES-orange)
<img width="1572" height="902" alt="Vista BootScribe v3 0 0 EN" src="https://github.com/user-attachments/assets/9445fd93-5430-4532-a0e8-9ba0a5c6f34e" />

-v3.1.0: Fixing some issues with the Lock and NVRAM.

A lightweight, portable, and open-source alternative to commercial UEFI management tools, developed in Python and `tkinter`.

This utility allows you to list, create, reorder, and configure UEFI boot entries (such as **rEFInd**, **GRUB**, or other Linux environments), as well as redirect or toggle the native Windows boot manager (`{bootmgr}`) directly in NVRAM using advanced `bcdedit` commands.

---

## Highlights & Key Features

- **Multi-language Support (ES / EN):** Bilingual interface with dynamic switching between **English** and **Spanish**.
- **Preview & Safe Confirmation:** Before applying critical modifications to NVRAM or `{bootmgr}`, BootScribe displays an exact preview of the commands and changes about to be executed, requiring explicit user confirmation.
- **NVRAM/UEFI Entry Listing:** Displays names, GUID identifiers, and full paths to `.efi` executables.
- **Universal Bootmgr Toggle:** Allows associating or releasing the path of the native Windows boot manager (`{bootmgr}`) to any custom bootloader (rEFInd, GRUB, etc.) following the official rEFInd documentation method.
- **Entry Injection:** Easily adds `.efi` executables from any accessible EFI System Partition (ESP).
- **Priority Management:** Modifies the global boot order (`fwbootmgr`) directly from Windows.
- **Direct Reboot to UEFI:** One-click option to reboot the machine directly into the BIOS/UEFI firmware interface (`shutdown /r /fw`).
- **Portable & Dependency-free:** Distributed as a ready-to-use executable package for Windows.

---

## How It Works (`{bootmgr}`)

On modern Windows systems with NVRAM and Secure Boot restrictions, the operating system often reverts boot order changes after an update. To solve this, BootScribe uses path redirection in `{bootmgr}`:

1. **Active Redirection:** Executes `bcdedit /set "{bootmgr}" path <EFI_PATH>` to force the primary loader to boot your preferred manager (e.g., `\EFI\refind\refind_x64.efi` or a GRUB binary).
2. **Restoration / Release:** Allows reverting the path back to the default Windows executable (`\EFI\Microsoft\Boot\bootmgfw.efi`) or switching to another entry without rigid configurations.

---

## How to Use BootScribe

1. **Download & Run:** Go to the [Releases](../../releases) section on GitHub, download the `.zip` file of the latest version, extract it, and run **`BootScribe.exe`** (it will automatically request Administrator privileges, which are required to interact with `bcdedit`).
2. **Select Language:** Choose between English or Spanish in the interface according to your preference.
3. **Manage Entries:** Review the list of UEFI entries detected in your NVRAM along with their GUIDs and paths.
4. **Universal Toggle or Editing:** Select your desired `.efi` executable (rEFInd, GRUB, etc.) and choose the action to perform.
5. **Review & Confirm:** Check the proposed changes in the confirmation window and accept them to write them to the system.

---

## Important Notes

> [!IMPORTANT]
> **Regarding Disabled Entries / rEFInd**  
> On modern Windows 10 and 11 systems, operating system security policies may prevent modifying the active state (_Active Flag_) of third-party UEFI entries directly from Windows.
>
> **Recommended workflow for new entries:**
>
> 1. Create and register your new entry (e.g., rEFInd) using **BootScribe**.
> 2. Use the direct reboot button to enter the **BIOS / UEFI** settings.
> 3. Enable the newly created entry and set it as first in the firmware priority order (_Boot Priority Order_).

> [!WARNING]
> **Regarding Windows Defender / SmartScreen**  
> Because BootScribe is a free, open-source tool compiled without a commercial digital signing certificate, Windows SmartScreen may show a warning when opening it for the first time. You can safely bypass it by clicking **"More info" → "Run anyway"**. All source code is fully available in this repository for auditing.
>
> **Known Behavior (`The NVRAM changed` dialog):**
> Shielding/redirecting `{bootmgr}` writes to NVRAM immediately. If you shield `{bootmgr}` **first** and then reorder boot priorities without refreshing, BootScribe's safety check will block saving with _"The NVRAM changed since the list was loaded"_ to prevent overwriting stale state.
>
> **How to avoid it:**
>
> 1. Either **reorder your boot priorities and apply/save first**, and **shield `{bootmgr}` last**; OR
> 2. If you already shielded `{bootmgr}`, click **Discard / Refresh** to reload the updated NVRAM state before reordering priorities.

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

## Recovery & Troubleshooting (If the System Fails to Boot)

If an invalid `.efi` path is configured or an entry becomes corrupted, Windows Boot Manager (`{bootmgr}`) may fail to load (displaying an error screen like `0xc000000f` or `0xc0000225`). You can restore any bootloader directly from your **BIOS/UEFI** or **Windows Recovery** without losing data:

### Method 1: Change Boot Priority in BIOS/UEFI (Fastest)

1. **Enter BIOS/UEFI:** Restart your PC and repeatedly press your motherboard's setup key (`F2`, `F12`, `DEL`, `ESC`, or `F10` depending on the manufacturer).
2. **Navigate to Boot Settings:** Go to the **Boot** or **Startup** tab.
3. **Select Boot Override / Priority:**
   - Look for **UEFI Hard Disk Drive BBS Priorities** or **Boot Option Priorities**.
   - Switch **Boot Option #1** directly to an intact entry (such as `Windows Boot Manager`, `ubuntu`, or `rEFInd`).
4. **Save & Exit:** Press `F10` to save changes and reboot. Once inside Windows, open **BootScribe** and fix or reset the `{bootmgr}` path.

### Method 2: Boot from a Specific `.efi` File (ASUS, Dell, HP, Lenovo, MSI)

Many modern UEFI firmwares include a built-in EFI file browser:

1. Enter your **BIOS/UEFI** setup or **One-Time Boot Menu** (`F12`, `F8`, `F11`, or `ESC`).
2. Select **Boot from EFI File** (or _Launch EFI Shell from filesystem device_).
3. Select your **EFI System Partition (ESP)** (usually a small `100MB–500MB` FAT32 partition).
4. Browse and select the `.efi` binary you want to boot:
   - **Windows Default:** `\EFI\Microsoft\Boot\bootmgfw.efi`
   - **rEFInd:** `\EFI\refind\refind_x64.efi`
   - **GRUB (Ubuntu/Debian):** `\EFI\ubuntu\grubx64.efi`

### Method 3: Restore `{bootmgr}` via Command Prompt (Windows Recovery / USB)

If `{bootmgr}` itself is broken and no other entry works:

1. Boot from a **Windows Installation USB** (or let Windows fail 3 times to trigger **Automatic Repair**).
2. Go to **Repair your computer** → **Troubleshoot** → **Advanced options** → **Command Prompt**.
3. Reset `{bootmgr}` to the default Windows bootloader:
   ```cmd
   bcdedit /set "{bootmgr}" path \EFI\Microsoft\Boot\bootmgfw.efi
   ```
4. _(Optional)_ If the EFI partition is damaged, rebuild the boot files completely:
   ```cmd
   diskpart
   list volume
   ```
   _(Identify the FAT32 volume of ~100MB-500MB, e.g., Volume 2, and assign it a letter)_:
   ```cmd
   select volume 2
   assign letter=Z:
   exit
   bcdboot C:\Windows /s Z: /f UEFI
   ```
5. Close the terminal and click **Continue** to boot normally into Windows.

---

---

# 🇪🇸 Español

# BootScribe v3.1.0

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011-0078D6?logo=windows&logoColor=white)
![License](https://img.shields.io/badge/License-Open%20Source-green)
![Language](https://img.shields.io/badge/Language-EN%20%7C%20ES-orange)
<img width="1575" height="902" alt="Vista BootScribe v3 0 0" src="https://github.com/user-attachments/assets/01772099-5629-4685-9357-178b8666c1f0" />

-v3.1.0: Corrección de algunos problemas con el bloqueo y la NVRAM.

Una alternativa ligera, portátil y de código abierto a las herramientas comerciales de gestión UEFI, desarrollada en Python y `tkinter`.

Esta utilidad permite listar, crear, reordenar y configurar entradas de arranque UEFI (como **rEFInd**, **GRUB** u otros entornos Linux), así como redirigir o conmutar el gestor de arranque nativo de Windows (`{bootmgr}`) directamente en la NVRAM mediante comandos avanzados de `bcdedit`.

---

## Novedades y Características Principales

- **Soporte Multi-idioma (ES / EN):** Interfaz bilingüe con cambio dinámico entre **Español** e **Inglés**.
- **Vista Previa y Confirmación Segura:** Antes de aplicar modificaciones críticas en la NVRAM o en `{bootmgr}`, BootScribe muestra una vista previa exacta de los comandos y cambios que se van a ejecutar, requiriendo confirmación explícita del usuario.
- **Listado de entradas NVRAM/UEFI:** Muestra nombres, identificadores GUID y rutas completas a ejecutables `.efi`.
- **Conmutación Universal de Bootmgr (Toggle):** Permite asociar o liberar la ruta del gestor nativo de Windows (`{bootmgr}`) hacia cualquier cargador de arranque personalizado (rEFInd, GRUB, etc.) siguiendo el método recomendado por la documentación de rEFInd.
- **Inyección de nuevas entradas:** Añade ejecutables `.efi` desde cualquier partición de sistema (ESP) accesible.
- **Gestión de prioridades:** Modifica el orden de arranque global (`fwbootmgr`) directamente desde Windows.
- **Reinicio directo a UEFI:** Un clic para reiniciar el equipo directamente en la interfaz del firmware BIOS/UEFI (`shutdown /r /fw`).
- **Portátil y sin dependencias:** Distribuido como paquete ejecutable listo para usar en Windows.

---

## Método de Funcionamiento (`{bootmgr}`)

En sistemas Windows modernos con restricciones de NVRAM y Secure Boot, el sistema operativo suele revertir los cambios en el orden de arranque tras una actualización. Para solucionarlo, BootScribe emplea la técnica de redirección de ruta en `{bootmgr}`:

1. **Redirección activa:** Ejecuta `bcdedit /set "{bootmgr}" path <RUTA_EFI>` para indicar al cargador principal que inicie el gestor de preferencia (por ejemplo, `\EFI\refind\refind_x64.efi` o un binario de GRUB).
2. **Restauración / Liberación:** Permite revertir la ruta al ejecutable predeterminado de Windows (`\EFI\Microsoft\Boot\bootmgfw.efi`) o alternar hacia otra entrada sin quedar atado a configuraciones rígidas.

---

## Cómo usar BootScribe

1. **Descargar y Ejecutar:** Ve a la sección de [Releases](../../releases) en GitHub, descarga el archivo `.zip` de la última versión, extráelo y ejecuta **`BootScribe.exe`** (solicitará permisos de Administrador automáticamente, necesarios para interactuar con `bcdedit`).
2. **Seleccionar Idioma:** Elige entre Español o Inglés según tu preferencia en la interfaz.
3. **Gestionar Entradas:** Revisa la lista de entradas UEFI detectadas en tu NVRAM con sus respectivos GUID y rutas.
4. **Conmutación Universal (Toggle) o Edición:** Selecciona el ejecutable `.efi` deseado (rEFInd, GRUB, etc.) y elige la acción a realizar.
5. **Revisar y Confirmar:** Verifica los cambios propuestos en la ventana de confirmación y acéptalos para escribirlos en el sistema.

---

## Notas Importantes

> [!IMPORTANT]
> **Sobre Entradas Deshabilitadas / rEFInd**  
> En sistemas modernos con Windows 10 y 11, las políticas de seguridad del sistema operativo pueden impedir modificar el estado de habilitación (_Active Flag_) de una entrada UEFI creada por terceros directamente desde Windows.
>
> **Flujo de trabajo recomendado para nuevas entradas:**
>
> 1. Crea y registra tu nueva entrada (ej. rEFInd) usando **BootScribe**.
> 2. Utiliza el botón de reinicio directo para ingresar a la configuración de la **BIOS / UEFI**.
> 3. Habilita la nueva entrada creada y selecciónala como primera en el orden de prioridad del firmware (_Boot Priority Order_).

> [!WARNING]
> **Sobre Windows Defender / SmartScreen**  
> Como BootScribe es una herramienta gratuita y de código abierto compilada sin un certificado comercial de firma digital, es posible que Windows SmartScreen muestre una advertencia al abrirlo por primera vez. Puedes omitirla de forma segura haciendo clic en **"Más información" → "Ejecutar de todos modos"**. Todo el código fuente está disponible en este repositorio para su auditoría.
>
> **Comportamiento Conocido (Alerta `The NVRAM changed`):**
> La función de blindar/redirigir `{bootmgr}` escribe en la NVRAM de forma inmediata. Si primero blindas `{bootmgr}` y luego modificas el orden de prioridades sin recargar la lista, la verificación de seguridad de BootScribe bloqueará el guardado mostrando _"The NVRAM changed since the list was loaded"_ para evitar sobrescribir un estado desactualizado.
>
> **Cómo evitarlo:**
>
> 1. **Modifica primero el orden de prioridades y guarda los cambios**, dejando el **blindaje de `{bootmgr}` como último paso**; O BIEN,
> 2. Si ya blindaste `{bootmgr}`, haz clic en **Descartar / Actualizar** para recargar el estado actual de la NVRAM antes de reordenar las entradas.

---

## Requisitos y Compilación desde el Código Fuente

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

## Recuperación y Solución de Problemas (Si el sistema no arranca)

Si se configura una ruta `.efi` inválida o una entrada se corrompe, el Gestor de Arranque de Windows (`{bootmgr}`) puede fallar al iniciar (mostrando un error como `0xc000000f` o `0xc0000225`). Puedes restaurar cualquier gestor de arranque directamente desde tu **BIOS/UEFI** o la **Recuperación de Windows** sin perder datos:

### Método 1: Cambiar la Prioridad de Arranque en BIOS/UEFI (El más rápido)

1. **Entrar a la BIOS/UEFI:** Reinicia tu PC y presiona repetidamente la tecla de configuración de tu placa madre (`F2`, `F12`, `SUPR/DEL`, `ESC` o `F10` según el fabricante).
2. **Ir a las opciones de arranque:** Dirígete a la pestaña **Boot** o **Startup**.
3. **Cambiar prioridad / Boot Override:**
   - Busca **UEFI Hard Disk Drive BBS Priorities** o **Boot Option Priorities**.
   - Cambia la **Opción de arranque #1 (Boot Option #1)** directamente hacia una entrada intacta (como `Windows Boot Manager`, `ubuntu` o `rEFInd`).
4. **Guardar y salir:** Presiona `F10` para guardar los cambios y reiniciar. Una vez dentro de Windows, abre **BootScribe** y corrige o restablece la ruta de `{bootmgr}`.

### Método 2: Arrancar desde un archivo `.efi` específico (ASUS, Dell, HP, Lenovo, MSI)

Muchos firmwares UEFI modernos incluyen un explorador de archivos EFI integrado:

1. Entra a la configuración de tu **BIOS/UEFI** o al **Menú de Arranque Único** (`F12`, `F8`, `F11` o `ESC`).
2. Selecciona **Boot from EFI File** (o _Arrancar desde archivo EFI_).
3. Elige tu **Partición del Sistema EFI (ESP)** (usualmente una partición FAT32 pequeña de `100MB a 500MB`).
4. Navega y selecciona el binario `.efi` que desees iniciar:
   - **Windows por defecto:** `\EFI\Microsoft\Boot\bootmgfw.efi`
   - **rEFInd:** `\EFI\refind\refind_x64.efi`
   - **GRUB (Ubuntu/Debian):** `\EFI\ubuntu\grubx64.efi`

### Método 3: Restaurar `{bootmgr}` desde Símbolo del Sistema (Recuperación de Windows / USB)

Si el propio `{bootmgr}` quedó dañado y ninguna otra entrada inicia:

1. Arranca desde un **USB de instalación de Windows** (o deja que Windows falle 3 veces seguidas para que abra la **Reparación automática**).
2. Ve a **Reparar el equipo** → **Solucionar problemas** → **Opciones avanzadas** → **Símbolo del sistema**.
3. Restablece `{bootmgr}` al cargador predeterminado de Windows:
   ```cmd
   bcdedit /set "{bootmgr}" path \EFI\Microsoft\Boot\bootmgfw.efi
   ```
4. _(Opcional)_ Si los archivos de arranque EFI se dañaron por completo, reconstrúyelos:
   ```cmd
   diskpart
   list volume
   ```
   _(Identifica el volumen FAT32 de ~100MB-500MB, por ejemplo Volumen 2, y asígnale una letra)_:
   ```cmd
   select volume 2
   assign letter=Z:
   exit
   bcdboot C:\Windows /s Z: /f UEFI
   ```
5. Cierra la consola y haz clic en **Continuar** para iniciar Windows normalmente.
