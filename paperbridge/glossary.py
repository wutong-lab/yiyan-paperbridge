"""Editable, domain-scoped starting vocabulary. Not an authoritative dictionary."""
import re
from .terminology import pack_terms

DOMAINS = {
    "general": {"name": "通用科研", "terms": {
        "confidence interval": "置信区间", "standard deviation": "标准差",
        "statistical significance": "统计显著性", "systematic uncertainty": "系统不确定度",
        "systematic error": "系统误差", "random error": "随机误差",
        "signal-to-noise ratio": "信噪比", "likelihood": "似然",
        "posterior distribution": "后验分布", "prior distribution": "先验分布",
        "Bayesian inference": "贝叶斯推断", "maximum likelihood": "最大似然",
        "control group": "对照组", "sample size": "样本量",
        "embarrassingly parallel": "易并行", "embarassingly parallel": "易并行",
        "European Union": "欧盟",
    }},
    "astronomy": {"name": "天文与物理", "terms": {
        "gravitational wave": "引力波", "gravitational waves": "引力波",
        "black hole": "黑洞", "black holes": "黑洞", "neutron star": "中子星",
        "supernova": "超新星", "circumstellar medium": "星周介质",
        "redshift": "红移", "light curve": "光变曲线", "light curves": "光变曲线",
        "luminosity": "光度", "effective temperature": "有效温度",
        "mass loss": "质量损失", "spectral energy distribution": "光谱能量分布",
        "dark matter": "暗物质", "dark energy": "暗能量",
        "power spectrum": "功率谱", "accretion disk": "吸积盘", "accretion disc": "吸积盘",
        "virial radius": "维里半径", "virial radii": "维里半径",
        "cosmology": "宇宙学", "astrophysics": "天体物理学",
        "astrophysical sources": "天体物理源", "astrophysical source": "天体物理源",
        "gravitational lensing": "引力透镜效应", "lensing": "透镜效应",
        "galaxy-scale": "星系尺度",
        "source populations": "源种群", "lens populations": "透镜种群",
        "population study": "群体研究", "population studies": "群体研究",
        "time-domain": "时域", "time domain": "时域",
        "time-domain survey": "时域巡天", "time-domain surveys": "时域巡天",
        "lensed quasar": "透镜类星体", "lensed quasars": "透镜类星体",
        "strongly lensed quasar": "强引力透镜类星体", "strongly lensed quasars": "强引力透镜类星体",
        "lensed supernova": "透镜超新星", "lensed supernovae": "透镜超新星",
        "lensed SNe": "透镜超新星", "lensed SN": "透镜超新星",
        "strongly lensed supernovae": "强引力透镜超新星",
        "velocity dispersion": "速度弥散", "intrinsic scatter": "内禀散布",
        "massive galaxy": "大质量星系", "massive galaxies": "大质量星系",
        "massive galaxy cluster": "大质量星系团", "massive galaxy clusters": "大质量星系团",
        "massive cluster of galaxies": "大质量星系团", "massive clusters of galaxies": "大质量星系团",
        "quad lens": "四像透镜系统", "quad lenses": "四像透镜系统",
        "intervening lenses": "沿传播路径上的透镜", "stellar-mass": "恒星质量",
        "intermediate-mass": "中等质量", "supermassive": "超大质量",
        "inspiralling": "旋近", "inspirals": "旋近", "inspiral": "旋近",
        "merger": "并合", "mergers": "并合", "ring-down": "振铃", "ringdown": "振铃",
        "numerical relativity": "数值相对论", "general relativity": "广义相对论",
        "standard siren": "标准汽笛", "standard sirens": "标准汽笛",
        "phenomenological waveform model": "唯象波形模型", "phenomenological": "唯象",
        "afterglow": "余辉", "initial mass function": "初始质量函数",
        "star formation rate density": "恒星形成率密度", "zero-age main sequence": "零龄主序",
        "evolution of massive binary stars": "大质量双星演化", "EMBS": "大质量双星演化",
        "eikonal approximation": "程函近似", "eccentricity": "偏心率",
        "minihalo": "微晕", "minihalos": "微晕", "dark matter minihalo": "暗物质微晕",
        "bright lensed siren": "明透镜汽笛", "dark lensed siren": "暗透镜汽笛",
        "Kirchhoff": "基尔霍夫", "Fresnel": "菲涅耳", "diffraction": "衍射",
        "geometric optics": "几何光学", "geometric-optics": "几何光学",
        "wave optics": "波动光学", "wave-optics": "波动光学",
        "compact binaries": "致密双星", "compact objects": "致密天体",
        "CBC": "致密双星并合", "CBCs": "致密双星并合事件",
        "BNS": "双中子星", "sBBH": "恒星质量双黑洞", "sBBHs": "恒星质量双黑洞",
        "BHNS": "黑洞–中子星双星", "SMBBH": "超大质量双黑洞", "SMBBHs": "超大质量双黑洞",
        "compact binary coalescences": "致密双星并合", "binary neutron stars": "双中子星",
        "binary black holes": "双黑洞", "primordial black holes": "原初黑洞",
        "dark matter halos": "暗物质晕", "mass function": "质量函数",
        "Hubble constant": "哈勃常数", "chirp mass": "啁啾质量",
        "magnification": "放大率", "magnifications": "放大率", "magnification bias": "放大率偏差",
        "multiple images": "多重像", "lensed images": "透镜像", "image pairs": "像对",
        "antenna pattern function": "天线响应函数", "antenna pattern functions": "天线响应函数",
        "optical depth": "光学深度", "time delay": "时间延迟", "time delays": "时间延迟",
        "waveform": "波形", "waveforms": "波形", "phase shift": "相移",
        "strong lensing": "强透镜效应", "microlensing": "微引力透镜效应",
        "subhalos": "次晕", "galaxy clusters": "星系团", "lens equation": "透镜方程",
        "equation of state": "状态方程", "kilonova": "千新星",
        "compact binary coalescence": "致密双星并合",
        "transverse-traceless gauge": "横向无迹规范", "post-Newtonian": "后牛顿",
        "luminosity distance": "光度距离", "angular diameter distance": "角直径距离",
        "pulsar timing arrays": "脉冲星计时阵列", "multimessenger astronomy": "多信使天文学",
        "Fermat": "费马", "Bayes factor": "贝叶斯因子", "false alarm": "虚警",
        "Einstein radius": "爱因斯坦半径", "Einstein Telescope": "爱因斯坦望远镜",
        "source plane": "源平面", "lens plane": "透镜平面", "thin lens": "薄透镜",
        "quasar": "类星体", "quasars": "类星体", "halo model": "晕模型",
        "halo mass function": "晕质量函数", "halo": "晕", "halos": "晕",
        "subhalo": "次晕", "tidal stripping": "潮汐剥离", "stellar mass": "恒星质量",
        "galaxy group": "星系群", "galaxy groups": "星系群", "star cluster": "星团",
        "group-scale": "星系群尺度", "cluster-scale": "星系团尺度",
        "group scale": "星系群尺度", "cluster scale": "星系团尺度",
        "microlensing variability": "微引力透镜光变", "intrinsic variability": "内禀光变",
        "inverse ray-shooting": "逆向光线追踪", "inverse ray shooting": "逆向光线追踪",
        "ray-shooting": "光线追踪", "ray shooting": "光线追踪",
        "mock catalog": "模拟星表", "mock catalogs": "模拟星表",
        "mock catalogue": "模拟星表", "mock catalogues": "模拟星表",
        "time-variable": "时变", "image separation": "像间距", "image separations": "像间距",
        "Hubble tension": "哈勃张力", "wide-field": "大视场",
        "Legacy Survey of Space and Time": "时空遗珍巡天",
        "critical curve": "临界曲线", "critical curves": "临界曲线",
        "caustic": "焦散", "caustics": "焦散", "micro-caustics": "微焦散",
        "reverberation mapping": "反响映射", "broad-line region": "宽线区",
        "broad line region": "宽线区", "broad emission lines": "宽发射线",
        "continuum emission": "连续谱辐射", "rest-frame": "静止系",
        "half-light radius": "半光半径", "effective radius": "有效半径",
        "magnification map": "放大率分布图", "magnification maps": "放大率分布图",
        "mass-sheet degeneracy": "质量片简并", "external shear": "外部剪切",
        "degeneracy": "简并", "degeneracies": "简并",
        "critical surface density": "临界面密度", "line-of-sight": "视线方向",
        "microlens": "微透镜", "microlenses": "微透镜",
        "singular isothermal sphere": "奇异等温球", "singular isothermal spherical": "奇异等温球",
    }},
    "biomed": {"name": "医学与生命科学", "terms": {
        "randomized controlled trial": "随机对照试验", "hazard ratio": "风险比",
        "odds ratio": "比值比", "relative risk": "相对风险",
        "overall survival": "总生存期", "progression-free survival": "无进展生存期",
        "gene expression": "基因表达", "single-cell sequencing": "单细胞测序",
        "adverse event": "不良事件", "informed consent": "知情同意",
        "primary endpoint": "主要终点", "double-blind": "双盲",
    }},
    "materials": {"name": "材料与化学", "terms": {
        "density functional theory": "密度泛函理论", "band gap": "带隙",
        "activation energy": "活化能", "catalytic activity": "催化活性",
        "tensile strength": "抗拉强度", "Young's modulus": "杨氏模量",
        "electrochemical impedance": "电化学阻抗", "charge transfer": "电荷转移",
        "specific capacity": "比容量", "thermal conductivity": "热导率",
    }},
    "engineering": {"name": "工程与计算机", "terms": {
        "finite element method": "有限元法", "boundary condition": "边界条件",
        "heat transfer": "传热", "neural network": "神经网络",
        "deep learning": "深度学习", "machine learning": "机器学习",
        "overfitting": "过拟合", "ablation study": "消融实验",
        "attention mechanism": "注意力机制", "computational complexity": "计算复杂度",
        "graph neural network": "图神经网络", "graph neural networks": "图神经网络",
        "reinforcement learning": "强化学习", "cross-validation": "交叉验证",
        "cross-view geo-localization": "跨视角地理定位", "cross-view geo localization": "跨视角地理定位",
        "image corruption": "图像退化", "image corruptions": "图像退化",
        "corrupted images": "退化图像", "corruption robustness": "退化鲁棒性",
        "fine-grained corruption robustness": "细粒度退化鲁棒性",
        "common corruption": "常见图像退化", "common corruptions": "常见图像退化",
        "corruption severity": "退化严重程度", "corruption severities": "退化严重程度",
        "convolutional neural networks": "卷积神经网络", "data augmentation": "数据增强",
        "Contrast Limited Adaptive Histogram Equalization": "对比度受限自适应直方图均衡化",
    }},
}


def parse_custom(text: str) -> dict:
    terms = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\t|\s*=\s*", line, maxsplit=1)
        if len(parts) != 2 or not all(x.strip() for x in parts):
            raise ValueError(f"术语表第 {number} 行格式有误，请使用：English term = 中文术语")
        source, target = [x.strip() for x in parts]
        if len(source) > 150 or len(target) > 150:
            raise ValueError(f"术语表第 {number} 行过长")
        terms[source] = target
    if len(terms) > 500:
        raise ValueError("每次最多使用 500 条自定义术语")
    return terms


def terms_for(domain: str, custom: str = "") -> dict:
    result = dict(DOMAINS["general"]["terms"])
    result.update(DOMAINS.get(domain, DOMAINS["general"])["terms"])
    result.update(pack_terms("general"))
    if domain in DOMAINS and domain != "general":
        result.update(pack_terms(domain))
    # Case-insensitive override, including the default vocabulary.
    for source, target in parse_custom(custom).items():
        result = {k: v for k, v in result.items() if k.casefold() != source.casefold()}
        result[source] = target
    return result


def matched_terms(text: str, terms: dict) -> dict:
    matches = {}
    occupied = []
    for source, target in sorted(terms.items(), key=lambda kv: -len(kv[0])):
        found = list(re.finditer(r"(?<![\w])" + re.escape(source) + r"(?![\w])", text, re.I))
        hits = [m for m in found if not any(m.start() < b and m.end() > a for a, b in occupied)]
        if hits:
            matches[source] = target
            occupied.extend((m.start(), m.end()) for m in hits)
    return matches


def contextual_glossary(text, terms, domain, formula_hints=None, reference=None):
    """Resolve known domain homonyms without matching a particular paper."""
    result = dict(terms)
    if domain in {"engineering", DOMAINS["engineering"]["name"]}:
        from .terminology import contextual_pack_terms
        reference = reference if isinstance(reference, dict) else {}
        table = reference.get("表格语境", {})
        evidence = " ".join(str(reference.get(key, "")) for key in
                            ["论文标题", "摘要背景", "当前章节"])
        evidence += " " + str(table.get("表题", "")) + " " + text
        vision = re.search(r"\b(?:images?|pixels?|computer vision|visual|aerial|geo[ -]?localization)\b", evidence, re.I)
        degradation = re.search(r"\b(?:corrupt\w*|robust\w*|blur|nois\w*|degrad\w*)\b", evidence, re.I)
        scopes = []
        if vision and degradation:
            scopes.append("image_degradation")
        # A short label inherits its actual geometric parent, never merely a
        # noise word elsewhere on the page (e.g. a camera's Shot column).
        if len(text.split()) <= 3 and len(text) <= 60:
            parent = str(table.get("上级表头", "")).strip().casefold()
            if parent in {"noise", "noises"}:
                scopes.append("noise_table_heading")
            elif parent in {"weather", "weather conditions"}:
                scopes.append("weather_table_heading")
        for scope in scopes:
            for phrase, target in contextual_pack_terms("engineering", scope).items():
                if not any(key.casefold() == phrase.casefold() for key in result):
                    result[phrase] = target
    if domain not in {"astronomy", DOMAINS["astronomy"]["name"]}:
        return result
    context = text + " " + " ".join((formula_hints or {}).values())
    if re.search(r"\b(?:lens(?:ing|ed)?|magnification|caustic|deflection)\b", text, re.I):
        for noun in ['image','images']:
            if not any(k.casefold()==noun for k in result):
                result[noun] = '像'
    # In simulated matter populations, render means instantiate objects.
    # Image/graphics rendering keeps its usual meaning; user choices prevail.
    if (re.search(r"\b(?:haloes|halos|subhaloes|subhalos)\b", text, re.I)
            and re.search(r"\b(?:population|point masses|mass fraction|total mass|number density)\b", text, re.I)
            and not re.search(r"\b(?:visualization|graphics|display)\b", text, re.I)):
        for phrase, target in {'render':'生成','rendered':'生成的','rendering area':'模拟区域'}.items():
            if not any(k.casefold() == phrase for k in result):
                result[phrase] = target
    # A geometrical or accretion torus is not automatically a dusty AGN torus.
    if re.search(r"\b(?:AGN|active galactic nucle\w*|quasar\w*)\b", context, re.I) and re.search(r"\bdust\w*\b", context, re.I) and not re.search(r"\baccretion\s+tor(?:us|i)\b", context, re.I):
        for phrase in ["torus", "tori"]:
            if not any(k.casefold() == phrase for k in result):
                result[phrase] = "尘埃环"
    lens_context = re.search(r"κ|\bkappa\b|\b(?:microlens\w*|shear|deflect(?:ion|ions|ing))\b|surface\s+(?:mass\s+)?density|lens equation|macro[ -]?image", context, re.I)
    if lens_context and re.search(r"\bconvergence\b", text, re.I) and not any(k.casefold() == "convergence" for k in result):
        result["convergence"] = "会聚度"
        # Longer phrases take precedence per occurrence, even in a paragraph
        # that discusses both physical convergence and numerical convergence.
        for phrase, target in {
            "numerical convergence": "数值收敛", "algorithm convergence": "算法收敛",
            "convergence test": "收敛测试", "convergence tests": "收敛测试",
            "convergence criterion": "收敛准则", "convergence criteria": "收敛准则",
            "convergence of the algorithm": "算法收敛",
        }.items():
            if not any(k.casefold() == phrase for k in result):
                result[phrase] = target
    return result


def normalize_line_hyphens(text, terms):
    """Restore wrapped words and spacing in known scientific phrases only."""
    known = {key.casefold() for key in terms}
    def join(match):
        compound = match[1] + "-" + match[2]
        return compound if compound.casefold() in known or any(k.startswith(compound.casefold() + " ") for k in known) else match[1] + match[2]
    text = re.sub(r"\b([A-Za-z]+)[-‐]\s+([a-z]+)\b", join, text)
    # A possessive and a following proper name can touch at a font boundary.
    text = re.sub(r"(?<=[A-Za-z])(['’]s)(?=[A-Z])", r"\1 ", text)
    # Font-run joins sometimes drop spaces between words. Restore a complete
    # known phrase, never infer arbitrary missing words or change formula tags.
    academic_phrases = {"we apply", "we use", "we find", "we present", "we propose", "we obtain",
                        "we derive", "we show", "our method", "our results", "this work", "this paper"}
    for phrase in sorted(set(terms) | academic_phrases, key=len, reverse=True):
        words = phrase.split()
        if len(words) < 2 or (sum(map(len, words)) < 8 and phrase not in academic_phrases) or not all(re.fullmatch(r"[A-Za-z-]+", word) for word in words):
            continue
        pattern = r"(?<!\w)" + r"\s*".join(map(re.escape, words)) + r"(?!\w)"
        def spaced(match):
            value = phrase
            if match[0].isupper():
                value = value.upper()
            elif match[0][0].isupper():
                value = value[0].upper() + value[1:]
            return value
        text = re.sub(pattern, spaced, text, flags=re.I)
    # Lower-case prepositions glued to quantities occur at PDF font boundaries;
    # capitalized astronomical identifiers such as TOI11 stay literal.
    if len(re.findall(r"[A-Za-z]{2,}", text)) >= 3:
        # A dimensional adjective at a PDF font boundary is prose only when
        # followed by a recognized task noun. Keep model IDs/code intact.
        text = re.sub(r"(?<![\w/@:.+-])(for|with|in|of|on|to)([23]D)(objects?|images?|reconstruction|detection)\b",
                      r"\1 \2 \3", text)
        text = re.sub(r"(?<![\w/@:.+-])([23]D)(objects?|images?|reconstruction|detection)\b",
                      r"\1 \2", text)
        text = re.sub(r"(?<![\w/@:.+-])(to|from|with|between|than|of|in|by|at|over|under|all)(?=\d)", r"\1 ", text)
        def conjunction(match):
            # Repair conjunction + acronym/model ID without splitting valid
            # code identifiers such as forEach, withDataFrame, or andThen.
            name = match[2]
            return match[1] + " " + name if re.search(r"\d|[A-Z].*[A-Z]", name) else match[0]
        text = re.sub(r"(?<![\w/@:.+-])(and|or)([A-Z][A-Za-z0-9_-]*\b)", conjunction, text)
    return text
