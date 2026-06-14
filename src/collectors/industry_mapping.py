"""申万一级行业 ↔ 新浪行业 的静态映射。

AkShare 的 stock_board_industry_cons_em（东财行业成分股）走 push2.eastmoney.com，
本地代理对该域名做了 TLS 指纹反爬，Python requests 不可用。
退而求其次用 Sina 行业（走 vip.stock.finance.sina.com.cn，可用），
然后通过本表映射回申万一级行业名。

策略需要的几个申万行业：
- 策略一（消费）：食品饮料 / 家用电器 / 美容护理 / 商贸零售
- 策略二（医药）：医疗器械 / 化学制药 / 生物制品
- 策略三（出海）：机械设备 / 汽车 / 基础化工

注意：
- 新浪"次新股"(new_stock) 和"其它行业"(new_qtxy) 不算真实行业，会跳过
- 新浪行业名比申万更细，所以是 多对一 关系
"""

# 申万一级 → 新浪行业代码列表
SW_FIRST_TO_SINA: dict[str, list[str]] = {
    "食品饮料": ["new_sphy", "new_ljhy"],
    "家用电器": ["new_jdhy"],
    "美容护理": ["new_fzxl"],
    "商贸零售": ["new_sybh", "new_wzwm"],
    "医药生物": ["new_swzz", "new_ylqx"],
    "机械设备": ["new_jxhy"],
    "汽车": ["new_qczz"],
    "基础化工": ["new_hghy", "new_hqhy"],
}

# 申万二级 → 新浪行业代码（仅策略二需要的部分）
SW_SECOND_TO_SINA: dict[str, list[str]] = {
    "医疗器械": ["new_ylqx"],
    "化学制药": ["new_swzz"],
    "生物制品": ["new_swzz"],
}

# 反向映射：新浪行业代码 → 申万一级行业名
# 当 Sina 行业不在策略关注范围时，用新浪行业名作为 fallback
SINA_TO_SW_FIRST: dict[str, str] = {}
for sw, sina_list in SW_FIRST_TO_SINA.items():
    for sina in sina_list:
        SINA_TO_SW_FIRST[sina] = sw

# 应该跳过的新浪板块（不是真实行业）
SINA_SKIP_LABELS: set[str] = {"new_stock", "new_qtxy"}


def sina_to_sw_first(sina_label: str, sina_name: str) -> str:
    """新浪行业代码 → 申万一级行业名。

    如果在映射表里找不到，返回新浪行业名本身作为 fallback（带 * 前缀标识）。
    """
    if sina_label in SINA_TO_SW_FIRST:
        return SINA_TO_SW_FIRST[sina_label]
    return f"*{sina_name}"
