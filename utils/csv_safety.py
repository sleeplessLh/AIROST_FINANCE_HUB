# ============================================================
# CSV Safety Utilities
# 防止 CSV Injection（Formula Injection）
# ============================================================

# Excel、Google Sheets 会把以这些字符开头的储存格当成公式执行
# \t（Tab）、\r（Carriage Return）也可能被用来绕过检查
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


# ============================================================
# 让文字在试算表中只会被当成"文字"
# Parameter：value，要写进 CSV 的值
# Return：安全的字符串
#   "=1+1"   → "'=1+1"（前面加单引号，试算表会显示成文字）
#   "Alice"  → "Alice"（不受影响）
# ============================================================
def neutralize_csv_formula(value):

    # None 写成空字符串；其他类型先转成字符串
    text = "" if value is None else str(value)

    # str.startswith() 可以接受 tuple，任何一个前缀符合就返回 True
    if text.startswith(FORMULA_PREFIXES):
        return "'" + text

    return text
