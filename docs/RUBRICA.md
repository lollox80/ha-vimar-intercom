# RUBRICA.md — La rubrica Vimar (`rubrica.db`): schema, estrazione, uso in HA

**Aggiornato:** 19 agosto 2026.

La `rubrica.db` è il database SQLite che l'app VIEW / il Tab usano per sapere **chi chiamare**,
**quali attuatori mostrare**, **con quale comando/target**, e **chi è l'SGA** (il destinatario dei
comandi di stato segreteria/DND). È la fonte di verità per rendere l'integrazione "universale":
gli attuatori HA, l'SGA e i target non sono indovinati, si leggono da qui.

Su questo impianto (**solo‑cloud**) la rubrica NON è ottenibile a runtime da HA (vedi §4). È stata
estratta una tantum rootando un muletto (§3), poi convertita con `tools/parse_rubrica.py` (§5) nella
lista JSON da incollare nelle opzioni dell'integrazione.

> **Nessuna credenziale in questo file.** Non riportare mai password SIP, `ha1`, token account,
> IMEI o PIN del Tab. I comandi `adb`/`su` qui sotto sono generici.

## 1. Come l'app la ottiene (dai sorgenti `com.vimar.vmsipsdk` decompilati) [APK]

Tre vie, a seconda del tipo di impianto (`CLOUD` nel QR: 0 home+cloud, 1 prima home poi cloud, 2 solo cloud):

- **Home mode** (`CLOUD=0/1`, LAN): `http://<proxy>/rest/get_file.php?name=rubrica` (HTTP Digest `sipID`/password),
  preceduto da mDNS discovery `_eipvdes._tcp` (TXT `domain/mac/proxy`). — **Su questo impianto :80 è muto** → via chiusa.
- **SIP**: `GET_INIT_STATUS` (Panda: blue → PICG) → `GET_INIT_STATUS_REPLY` contiene `token` e `rubrica_ver`,
  poi download cloud (sotto). — **Su questo impianto la reply non arriva** → via chiusa.
- **Cloud phonebook**: `GET https://<cproxy>/phonebook/domains/<cdomain_senza_.cproxy>/<ver>` con
  **HTTP Digest user=`<cDomain>` pass=`<token>`** (`VMClientRepository.startCloudDownloadRubrica`).
  Il `token` nel SDK viene SOLO da `GET_INIT_STATUS_REPLY` o da `get_info.php?action=status` → entrambi
  assenti qui: in cloud il token è **provisionato dal layer app `com.vimar.view`** (account OIDC
  `prod.vimar.cloud`), non reversato nel SDK. → via aperta ma bloccata sul token (vedi ROADMAP 3.1.0).

## 2. Schema (23 tabelle) — dati reali di questo impianto + note universali

| Tabella | Colonne principali | Contenuto qui / note |
|---|---|---|
| `ACTUATOR_LIST` | `ID, NAME, GID_PE, ATT_ID, MSG, DTMF, OUTPUT, TIME, ICON` | **6 attuatori, tutti `GID_PE=55001`**: Serratura(`MSG=OPEN`, dtmf `*0000`, ICON=1 DOOR), TIRO(`TARGA_ULTIMA_SERRATURA`, DOOR), "F1 ultima targa"(`TARGA_ULTIMA_F1`, ICON=3 SWITCH), "F2 ultima targa"(`TARGA_ULTIMA_F2`, SWITCH), "LUCE SCALA"(`ATTUATORE_01`, SWITCH), "Attuatore 02"(`ATTUATORE_02`, SWITCH). `TIME=500` (ms). **Il body SIP inviato = `MSG` letterale**, `Panda: command`, verso `GID_PE`. `ATT_ID` = id modulo, NON il target. |
| `ACTUATOR_RULES` | `ID, GA_GID, ACTUATOR_ID` | quali attuatori vede il GID appartamento (101). |
| `ACTUATOR_DEFAULT` | `PRODUCT, TERMINAL, MSG, DTMF` | comandi default per modulo: AV→`OPEN`(*0010), Keyboard→`AUX1`(*0011), RFID→`AUX2`(*0012), FP→`AUX3`(*0013). |
| `ICON_LIST` | `ID, NAME` | 1=DOOR, 2=LIGHT, 3=SWITCH → mappate a device_class/icona in HA. |
| `PHONEBOOK` | `GID, TYPE, MID, NAME, …ENABLE, GATE…` | 101=Casa(GA), **55001=Casa CG (PICG=SGA)**, **55100=Video (PE targa/camera, ENABLE=1, GATE=1)**, 55200=Centralino(P). |
| `PID_LIST` | `…PID, PID_ROLE…` | appartamento 101 → PID **55001**, role **PICG**. |
| `SYSTEM` | `PARAM, VALUE` | `MAGIC_APT_INTERCOM=55001` (**SGA**), `VM_PREFIX=8000`, `SCHEMA_VER=1`, `DB_VER=0`. |
| `PRODUCT_LIST` | `PRODUCT, …` | TAB 7S=40607, TAB 7=40605, TAB 4.3S=40603, TAB 4.3=40601; moduli AV=41006, Tastiera=41019, RFID=41017, FP=41016, Tastiera CA=41020, LCD=41018, Relè=40636. |
| `TVCC_LIST` / `TVCC_RULES` | — | telecamere IP: **vuote qui**, schema noto per impianti che le hanno. |
| `ENTRANCE_LIST` | — | ingressi/pulsantiere: vuota qui. |
| `INPUT_LIST` / `INPUT_DEFAULT` | — | ingressi/allarmi: vuoti qui. |
| `MONITORING` / `MONITORING_LIST` | — | monitoraggi: vuoti qui. |
| `PLAYLIST` / `ACTUATOR_PLAYLIST` | — | sequenze attuatori: vuote qui. |
| `ACTION_LOG`, `DENY_IP_LIST` | — | vuote qui. |
| `BUILDING_LIST`, `MID_LIST`, `STAIR_LIST`, `ZONE_LIST` | — | contenitori gerarchici (default 911). |

> ⚠ **Attenzione ai due 55xxx:** la **camera/targa reale è 55100**; **55001 è il PICG/capogruppo** (SGA)
> a cui vanno attuatori e comandi di stato. Non confonderli.

## 3. Estrazione via root (metodo riproducibile) — muletto Samsung A32

L'app ha `allowBackup=false`, quindi `adb backup` non funziona: serve accesso `su` al filesystem.
Metodo usato (senza credenziali):

1. **Muletto**: Samsung A32 `SM-A325F`, Android 13. Root con **Magisk**: sblocco bootloader
   (OEM unlocking), `AP` patchato con Magisk e flashato via **Odin**.
2. Installare **VIEW** (`com.vimar.view`) e fare **login con l'account Vimar dell'utente**.
   L'app registra ogni nuovo device con un **id SIP nuovo** (es. 60993) e salva le credenziali
   come `ha1` nel `linphonerc` (mai in chiaro).
3. Copiare il db con `adb` + `su`:
   ```bash
   adb shell "su -c 'ls -1 /data/data/com.vimar.view/files/*_rubrica.db'"
   adb shell "su -c 'cp /data/data/com.vimar.view/files/<domain>_rubrica.db /sdcard/rubrica.db'"
   adb pull /sdcard/rubrica.db ./rubrica.db
   ```
   (Il prefisso `<domain>` è il `CDOMAIN` dell'impianto.)
4. **Non** copiare `linphonerc`/prefs nel repo: contengono `ha1` e device id.

## 4. Perché su questo impianto le altre vie sono chiuse

- **Home HTTP** (:80 del Tab): accetta il TCP ma non risponde (read timeout) — l'impianto è solo‑cloud, la home mode è disattivata.
- **SIP `GET_INIT_STATUS`**: nessuna `GET_INIT_STATUS_REPLY` (manca un PICG che la generi lato SIP; il Tab fa solo 200‑ACK).
- **`adb backup`**: bloccato da `allowBackup=false`.
- **Cloud phonebook**: endpoint noto, ma il `token` è provisionato dal layer app/account, non ricavabile dal SDK.

## 5. Da `rubrica.db` a opzioni HA — `tools/parse_rubrica.py`

```bash
python tools/parse_rubrica.py rubrica.db --gid 101 --json
```
- apre il db **read‑only**, non stampa credenziali;
- estrae gli **attuatori** visibili al GID appartamento come lista JSON `{name, msg, target, icon}`
  (`msg` = body SIP letterale, `target` = `GID_PE`, `icon` = door|light|switch da `ICON_LIST`);
- riporta l'**SGA** reale (`SYSTEM.MAGIC_APT_INTERCOM`) e i parametri `SYSTEM` utili.

L'output JSON si incolla nell'**options flow** dell'integrazione (campo "Attuatori (JSON)"):
crea i bottoni dinamici (`button.py` → `VimarActuatorButton`), inviati con `hub.async_send_command`
(`Panda: command`). `target: "AUTO"` usa la targa di default dell'apri‑porta.

## 6. Rendere l'importazione "universale" (ROADMAP)

- ~~Servizio/importer `rubrica.db` → `options["actuators"]` senza copia‑incolla manuale.~~ **Fatto 07/09/2026**: menu opzioni → "Importa attuatori da rubrica.db" (vedi `config_flow.py`, modulo `rubrica_import.py`). Estrae e mostra anche l'SGA rilevato, ma non lo applica: resta da fare il punto successivo.
- Auto‑detect `sga_target` da `SYSTEM.MAGIC_APT_INTERCOM` (oggi default 55001 in `const.py`).
- Mappatura `ICON_LIST` → device_class HA; supporto `TIME`/`DTMF`.
- Entità derivate dal `PHONEBOOK` (targhe/camere 551xx, centralino, appartamento) + supporto `TVCC_LIST` dove presente.
- Import cloud phonebook quando il `token` è disponibile (impianti con SIP reply o home HTTP).
