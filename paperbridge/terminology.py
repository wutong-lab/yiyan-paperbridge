"""Versioned local terminology packs with optional primary-source references."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

PACKS = Path(__file__).with_name("terminology")


@lru_cache(maxsize=8)
def load_pack(domain):
    path = PACKS / f"{domain}.json"
    return json.loads(path.read_text()) if path.is_file() else {"entries": [], "sources": {}}


def pack_terms(domain):
    return {source: item["chinese"] for item in load_pack(domain)["entries"]
            if not item.get("context")
            for source in [item["english"], *item.get("variants", [])]}


def contextual_pack_terms(domain, context):
    return {source: item["chinese"] for item in load_pack(domain)["entries"]
            if item.get("context") == context
            for source in [item["english"], *item.get("variants", [])]}


def terminology_entries(domain):
    entries = []
    for scope in dict.fromkeys(["general", domain]):
        pack = load_pack(scope)
        for item in pack["entries"]:
            entry = {**item, "domain": scope}
            entry["sources"] = [pack["sources"][key] for key in item.get("source_ids", [])]
            entries.append(entry)
    return entries


def atomic_terms(text, terms):
    # Only curated noun phrases are substituted. Ambiguous words, verbs and
    # arbitrary user entries remain visible to the model for grammatical use.
    names = {source.casefold() for scope in ["general", "astronomy", "engineering"]
             for item in load_pack(scope)["entries"] if item.get("atomic")
             for source in [item["english"], *item.get("variants", [])]}
    from .glossary import matched_terms
    return {key: value for key, value in matched_terms(text, terms).items() if key.casefold() in names}


def enforced_terms(text, terms):
    """Explicit noun anchors; domain/context activation still belongs to glossary.

    Keep only opted-in, unambiguous terminology fixed in longer paragraphs.
    Verbs and arbitrary user phrases retain the existing grammatical path.
    """
    from .glossary import matched_terms
    names = {source.casefold() for scope in ["general", "astronomy", "engineering"]
             for item in load_pack(scope)["entries"] if item.get("enforce")
             for source in [item["english"], *item.get("variants", [])]}
    anchors = {key: value for key, value in terms.items() if key.casefold() in names}
    import re
    # Preserve the glossary's longest complete phrase. Binding only a suffix
    # such as "corruptions" would break the prescribed "common corruptions"
    # or "corruption severities" translation and create artificial warnings.
    return {key: value for key, value in matched_terms(text, terms).items()
            if key.casefold() in names or any(target in value and re.search(
                r"(?<!\w)" + re.escape(source) + r"(?!\w)", key, re.I)
                for source, target in anchors.items())}


def counted_terms(text, terms):
    """Keep common counts attached to their objects, across scientific fields.

    Protecting 'three' and 'four' separately can preserve both values yet swap
    their objects. Complete counted noun phrases are semantic atoms instead.
    Units and unknown noun phrases are not forced into countable categories.
    """
    import re
    nouns = {
        'flux ratio': ('通量比', '个'), 'flux ratios': ('通量比', '个'),
        'image': ('图像', '幅'), 'images': ('图像', '幅'),
        'parameter': ('参数', '个'), 'parameters': ('参数', '个'),
        'sample': ('样本', '个'), 'samples': ('样本', '个'),
        'model': ('模型', '个'), 'models': ('模型', '个'),
        'model parameter': ('模型参数', '个'), 'model parameters': ('模型参数', '个'),
        'model realization': ('模型实例', '个'), 'model realizations': ('模型实例', '个'),
        'dataset': ('数据集', '个'), 'datasets': ('数据集', '个'),
        'event': ('事件', '个'), 'events': ('事件', '个'),
        'galaxy': ('星系', '个'), 'galaxies': ('星系', '个'),
        'quasar': ('类星体', '个'), 'quasars': ('类星体', '个'),
    }
    words = {'one':'一','two':'两','three':'三','four':'四','five':'五','six':'六','seven':'七','eight':'八','nine':'九','ten':'十'}
    # A final component of a section number or decimal is not a count.
    pattern = r'(?<![\w.-])(' + '|'.join(words) + r'|\d+)\s+(' + '|'.join(re.escape(n) for n in sorted(nouns,key=len,reverse=True)) + r')(?![\w-])'
    result = {}
    for match in re.finditer(pattern,text,re.I):
        # Do not bind a noun modifier as the counted object, e.g. the image
        # in 'four image samples'. Unknown continuations stay visible.
        following = re.match(r'\s+([A-Za-z]+)', text[match.end():])
        boundaries = {'between','of','in','at','for','from','with','without','and','or','that','which','are','is','were','was','to','using','as','by','per','across','along','through','each'}
        if following and following[1].casefold() not in boundaries:
            continue
        noun = match[2].casefold()
        target, classifier = nouns[noun]
        target = next((v for k,v in terms.items() if k.casefold()==noun), target)
        if target == '像': classifier = '个'
        result[match[0]] = words.get(match[1].casefold(),match[1]) + classifier + target
    return result


def geometric_reference_terms(text, terms=None):
    """Bind an explicitly stated aperture reference case as one noun phrase.

    This narrow grammar keeps a reference mass paired with its aperture while
    leaving the actual square-root scaling clause visible for translation.
    No radius/diameter interpretation, values or units are inferred.
    """
    import re
    pattern = (r'\bcircular\s+aperture\s+of\s+'
               r'(?P<size>\d+(?:\.\d+)?\s*(?:"|″|arcsec(?:onds?)?))\s+for\s+'
               r'(?P<mass>\{v\d+\}\s*M\s*\{v\d+\})'
               r'(?=\s*,\s*scaling\s+with\s+(?:the\s+)?(?:square\s+root|root)\b)')
    aperture = next((v for k,v in (terms or {}).items() if k.casefold() == 'aperture'), '孔径')
    return {m[0]: f"以{m['mass']}为质量基准、{aperture}为{m['size']}的圆形区域"
            for m in re.finditer(pattern,text,re.I)}


def calendar_terms(text):
    """Translate explicit calendar dates as a unit, including PDF word joins.

    Chinese month names keep the original numeric multiset unchanged, while
    preventing day ranges from being interpreted as a range of months.
    Impossible dates remain visible rather than being silently corrected.
    """
    import calendar
    import re
    months = ['January','February','March','April','May','June','July','August','September','October','November','December']
    chinese = ['一','二','三','四','五','六','七','八','九','十','十一','十二']
    pattern = (r'(?<![\w.-])(?P<start>\d{1,2})(?:st|nd|rd|th)?'
               r'(?:\s*[-–—]\s*(?P<end>\d{1,2})(?:st|nd|rd|th)?)?\s+'
               r'(?:of\s+)?(?P<month>' + '|'.join(months) + r')\s*(?P<year>(?:19|20)\d{2})(?!\w)')
    result = {}
    for m in re.finditer(pattern,text,re.I):
        month = next(i+1 for i,name in enumerate(months) if name.casefold()==m['month'].casefold())
        start,end = int(m['start']),int(m['end'] or m['start'])
        if not 1 <= start <= end <= calendar.monthrange(int(m['year']),month)[1]:
            continue
        day = m['start'] + ('日至' + m['end'] if m['end'] else '')
        result[m[0]] = m['year'] + '年' + chinese[month-1] + '月' + day + '日'
    # Fractional UT dates and month-first dates also form one protected unit.
    month_pattern = '|'.join(months)
    day_pattern = r'\d{1,2}(?:\.\d+)?'
    patterns = [
        rf'(?<![\w.-])(?P<year>(?:19|20)\d{{2}})\s+(?P<month>{month_pattern})\s+(?P<day>{day_pattern})(?!\w|\.\d)',
        rf'(?<![\w.-])(?P<month>{month_pattern})\s+(?P<day>{day_pattern})\s*,\s*(?P<year>(?:19|20)\d{{2}})(?!\w)',
        rf'(?<![\w.-])(?P<month>{month_pattern})\s+(?P<day>{day_pattern})(?!\w|\.\d)',
    ]
    for date_pattern in patterns:
        for m in re.finditer(date_pattern, text, re.I):
            month = next(i+1 for i,name in enumerate(months) if name.casefold()==m['month'].casefold())
            year = m.groupdict().get('year')
            if not year and re.search(r'\b(?:19|20)\d{2}\s+$', text[:m.start()]):
                continue  # Do not salvage part of an impossible explicit date.
            if not 1 <= float(m['day']) < calendar.monthrange(int(year or 2000), month)[1] + 1:
                continue
            result[m[0]] = (year + '年' if year else '') + chinese[month-1] + '月' + m['day'] + '日'
    return result


def multiplicative_terms(text):
    """Keep multiplicative uncertainty from becoming a countable object."""
    import re
    words={'two':'两','three':'三','four':'四','five':'五','six':'六','seven':'七','eight':'八','nine':'九','ten':'十'}
    pattern = (r'(?<!\w)uncertain\s+by\s+a\s+factor\s+of\s+'
               r'(?P<number>' + '|'.join(words) + r'|\d+(?:\.\d+)?)(?!\w|\.\d|[-–]\d)')
    result={}
    for m in re.finditer(pattern,text,re.I):
        factor=words.get(m['number'].casefold(),m['number']) + '倍'
        result[m[0]]='具有' + factor + '的不确定性'
    return result
