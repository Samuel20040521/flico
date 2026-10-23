# AGENTS.md — flico-fresh repo 交接

一句話:這是 flico 專案的**線性故事版本**,從 `/Users/dengensheng/Developer/SOC_Final/`
重寫而來。每個 commit ≈ 一天的開發事件,搭配研究日誌敘事呈現專案歷程。

flico(*flicker* + *pico*)= Raspberry Pi Pico + BPW34 光二極體 + MCP6004 TIA 做成的螢幕 /
光源閃爍量測儀。

## 這個 repo 的角色 vs SOC_Final

| | flico-fresh(本 repo) | SOC_Final |
|---|---|---|
| 性質 | 線性故事版,給人看的 | 工作 repo,真實開發狀態 |
| commit 粒度 | 一天一個 entry / 主題 | 不規律 |
| 目錄結構 | 角色分組(firmware/host/hardware/…) | 同上 + 雜亂 `pico-project/` 過渡資料夾 |
| 含 | 整理過的素材、研究日誌、PDF | 全部源始 + 89 個 KiCad 自動 zip + `.history/` + build artifacts |
| 用途 | 報告交付、回溯歷程 | 真正幹活的地方 |

**重要:寫新內容前,先到 `/Users/dengensheng/Developer/SOC_Final/` 撈原始素材**
(Gemini 對話、量測 CSV、KiCad 檔、影片、Fusion 腳本)。

## 子資料夾速覽 + SOC_Final 對映

| flico-fresh 路徑 | 內容 | SOC_Final 來源 |
|---|---|---|
| `firmware/cpp-version/` | Pico SDK C++ 韌體(主要版本,SSD1306 + ADC + USB-CDC) | `firmware/` |
| `firmware/led-light-test-cpp-version/` | C++ blink 試水溫 | `pico-project/blink/` |
| `firmware/led-light-test-python-version/` | MicroPython blink(5/8 環境驗證) | `pico-project/led-light-test/` |
| `firmware/python-version/` | MicroPython 版主程式(被 cpp-version 取代,留作歷史) | `pico-project/Pico_Flicker/{main.py,ssd1306.py}` |
| `hardware/first-version/` | KiCad GUI workflow 起手版(5/8 開,放棄) | `hardware/shield/pico_flicker_shield/` |
| `hardware/second-version/` | SKiDL 程式化重寫的 PD\_detector\_pro(5/18,放棄) | `pico-project/PD_detector_pro/` |
| `hardware/final-version/` | 最終投產版 SOC\_Final(5/20–25,JLCPCB 已送) | `hardware/main_board/` |
| `host/` | Rust GUI (`rust_flico`) + Python 舊版 `read_data.py` + 量測噪音 PWL pipeline | `host/` + `pico-project/Pico_Flicker/read_data.py` |
| `mechanical/` | Fusion 360 Python script(parametric fixture,進行中) | `mechanical/` |
| `simulation/` | PySpice TIA 模擬(完整) | `simulation/` |
| `.github/workflows/release-rust_flico.yml` | Rust GUI 跨平台發佈 CI | 同名 |
| `report/research-log/` | XeLaTeX 研究日誌(4/19 → 5/20 已寫) | 部分原始 + 部分新撰寫 |

### 命名約定

- **資料夾不加 `flico-` 前綴**,因為 repo 本身即 flico
- **`hardware/{first,second,final}-version/`**:三版線性命名,反映開發歷程,非語意分類
- **`firmware/{cpp,python}-version/`、`firmware/led-light-test-{cpp,python}-version/`**:
  Pico SDK vs MicroPython 雙路線並存,兩條都在
- **KiCad project stem 不改名**(`SOC_Final.kicad_*` 留著)—— KiCad internal
  cross-references 跟 stem 綁死,改 stem blast radius 太大

## 寫研究日誌的模板

1. 寫 `report/research-log/entries/YYYY-MM-DD.tex`
2. 圖 / 影片放 `report/research-log/figures/YYYY-MM-DD/`
3. Gemini 對話 raw txt 放 `report/research-log/gemini_discuss_history/MMDD[-suffix].txt`
4. 在 `report/research-log/log.tex` 條目區塊加兩行:
   ```latex
   \logentry{YYYY-MM-DD}{當日標題}
   \input{entries/YYYY-MM-DD}
   ```
5. 編譯驗證:`xelatex log.tex && biber log && xelatex log.tex && xelatex log.tex`

每個 entry 一個 commit,commit message 用 `Add YYYY-MM-DD entry: <一句話標題>`。
**多天揉成一篇是例外**(5/20 那篇 6 天合一,因為「同一件事的連續工作」)。

## 跨資料夾耦合(維持 SOC_Final 的耦合圖)

- **firmware ↔ host**:USB-CDC 序列協定(ASCII / 簡單 CSV)
- **simulation ↔ hardware/final-version**:PySpice 是 TIA 的數值模型
- **host ↔ simulation**:`host/convert.py` 把實測 CSV 轉成 KiCad PWL,餵
  `hardware/final-version/TIA_Gain_100M.wbk`(**5/20 entry 把這套方法論講成最得意的成果**)
- **report ↔ 全部**:`report/main.tex`(尚未搬入,見下)引用各路產物

## 未完成 / 待轉移(2026-06-07 更新)

### ✓ 已補(2026-06-07 那天一次補完)

- **A** host/ Rust 版主體(`Cargo.toml` / `Cargo.lock` / `index.html` / `src/main.rs` / `.gitignore`)—— commit `533ab62` companion to 5/19
- **B** host/convert.py + kicad_noise_pwl.txt + 4 個 baseline CSV(force-add,因為 `host/.gitignore` 把 csv/pwl 排除作為運行時產物模板)—— commit `90ecc45` companion to 5/20
- **C** mechanical/ stubs(fusion_pico_fixture.py/manifest/bkp/icon)+ `log.tex` 末加 `\section*{展望}` —— commit `8dd93a0`
- **E** `.github/workflows/release-rust_flico.yml` —— commit `508cf32`
- **F** 5 個 Gemini 對話 txt amend 進對應 entry commit(5/7、5/8、5/12 ×3)—— rebase 改寫,branch 未推送
- **H** 根 `README.md`(專案介紹 + 資料夾結構 + 在地跑通指令)—— commit `2c59589`
- **D** `report/main.tex` 期末主報告 + `references.bib`(從零撰寫,SOC_Final 那版只是空模板)—— commit `3129000`,10 頁
- **\~** `pico-project/main.py` 確認**不搬**(使用者 6/7 拍板)—— 內容是 5/8 那條 `while True: print(...)` 風格的 stub,功能已被 `firmware/python-version/main.py` 取代

### ❗ 還沒做

| # | 缺什麼 | SOC_Final 來源 |
|---|---|---|
| G | `entries/2026-06-02.tex`(疑為研究日誌起手條目,跟 `log.tex` `\date{起始於 2026 年 6 月 2 日}` 對應) | `SOC_Final/report/research-log/entries/2026-06-02.tex` |

### 刻意不搬(已併入新位置或屬於雜物)

- `pico-project/{blink, PD_detector_pro, Pico_Flicker}/` —— 已拆散歸到 firmware/ /
  hardware/,不需要重新出現
- `pico-project/Pico_Flicker/{demo_serial.py, pyproject.toml, uv.lock, README.md,
  claude_history.txt, flicker_20260512_155023.csv}` —— Python 包雜項,可選擇性併入
- `pico-project/main.py`(根目錄)—— **用途不明,需要使用者確認**
- `.vscode/`、`.venv/`、`.python-version`、`SOC_Final-backups/`、`.bak_before_*`、
  `.history/`、build artifacts —— 不入版控

## 不要做的事

- 不要憑空寫研究日誌 —— 沒有原始素材(對話 / 量測 / 截圖)就先問使用者
- 不要動已完成的 entry,除非使用者明說 —— 時間軸是線性的
- 不要把 SOC_Final 的 `.history/`、`.bak_*`、`*-backups/` 搬過來
- 不要在沒問清楚的狀況下幫 host/ 加 Rust 程式碼 —— 5/19 entry 已預告會搬,但要哪個版本要先問
- 不要改 KiCad project stem(`SOC_Final.kicad_*`)
- 不要改 `mechanical/fusion_pico_fixture.manifest`(Fusion 360 嵌入式腳本清單,改檔名會壞)

## 目前 commit 狀態(19 個,2026-06-07 backfill 之後)

```
<HEAD>  Add README.md
0d06a1e Add AGENTS.md
8dd93a0 Add mechanical/ stubs + 展望 section: future-work response to maker spirit
508cf32 Add release-rust_flico CI workflow
90ecc45 Add host/convert.py + measured-noise PWL + 4 baseline CSVs: companion to 2026-05-20 entry
533ab62 Add host/ Rust GUI crate (rust_flico): companion to 2026-05-19 entry
fd9059f Add 2026-05-20 entry: final PCB (SOC_Final) — design, simulation, JLCPCB submission on 5/25
8e7657c Rename PD_detector_pro to second-version; add pico_flicker_shield as the real first-version
8264fb0 Add 2026-05-19 entry: rewrite host/ from Python (matplotlib → PyQtGraph) to Rust
93f7a09 Add 2026-05-18 entry: design first-version PCB (PD_detector_pro)
74fd701 Add 2026-05-12 entry: solder day, final breadboard, fork to C++ Pico SDK   [amended: +3 gemini txts]
98153bd Add 2026-05-11 entry: second Guanghua trip + first OLED bring-up + MUX insight
169cac0 Add 2026-05-08 entry: Pico + MicroPython signal chain (no OLED yet)         [amended: +0508.txt]
d810616 Add firmware/led-light-test/blink.py: MicroPython + VS Code Pico hello world
91577ec Add 2026-05-07 entry: soldering iron selection (TS101/TS21 → HS-02A)        [amended: +0507.txt]
3386bc4 Add 0506.txt gemini chat history (companion to 2026-05-06 entry)
3755682 Add 2026-05-06 entry: bench TIA + PySpice cross-validation
4545934 Add 2026-04-21 entry: first shopping trip to Guanghua market
c8fd202 Initialize flico research log with 2026-04-19 brainstorm entry
```

> 6/7 那天有對 5/7、5/8、5/12 三個 commit 做 `git commit --amend` 把對應的 Gemini
> 對話 txt 嵌進去(透過 `git rebase --onto`),所以從 5/7 之後的所有 commit hash
> 都已改變。Branch 還沒推遠端,改寫歷史只影響本地。
