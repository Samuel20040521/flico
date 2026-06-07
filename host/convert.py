import pandas as pd

# 讀取原始檔案
df = pd.read_csv('flicker_1779254893.csv')

# 1. 處理時間 (轉換為秒，並強制從 0 開始)
t_min = df['t_ms'].min()
time_s = (df['t_ms'] - t_min) / 1000.0

# 2. 處理電壓 (去直流平均值，並放大 1000 倍還原成實際的 V 單位)
v_mean = df['value'].mean()
voltage_noise = (df['value'] - v_mean) * 1000.0

# 3. 建立新的 DataFrame
df_pwl = pd.DataFrame({
    'time': time_s.map('{:.6f}'.format),     # 保留小數點後 6 位
    'voltage': voltage_noise.map('{:.6f}'.format)
})

# 4. 輸出為 KiCad PWL 格式 (以空白分隔，無標題行)
df_pwl.to_csv('kicad_noise_pwl.txt', sep=' ', index=False, header=False)
print("轉換完成！檔案已存為 kicad_noise_pwl.txt")
