"""Small ComicInfo 2.0 editor model; preserve unedited and unfamiliar XML."""
from __future__ import annotations
import copy
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
import xml.etree.ElementTree as ET

MAX_XML_BYTES = 2 * 1024 * 1024
FIELDS = ('Title Series Number Count Volume AlternateSeries AlternateNumber AlternateCount Summary Notes '
          'Year Month Day Writer Penciller Inker Colorist Letterer CoverArtist Editor Publisher Imprint Genre '
          'Web PageCount LanguageISO Format BlackAndWhite Manga Characters Teams Locations ScanInformation '
          'StoryArc SeriesGroup AgeRating Pages CommunityRating MainCharacterOrTeam Review').split()
BASIC = ('Series', 'Title', 'Number', 'Volume', 'Publisher', 'Year', 'Month', 'Day', 'LanguageISO',
         'Summary', 'Writer', 'Penciller', 'Inker', 'Colorist', 'Letterer', 'CoverArtist', 'Editor')
INTEGER = {'Count', 'Volume', 'AlternateCount', 'Year', 'Month', 'Day'}
ENUMS = {'BlackAndWhite': ('Unknown', 'No', 'Yes'),
         'Manga': ('Unknown', 'No', 'Yes', 'YesAndRightToLeft'),
         'AgeRating': ('Unknown', 'Adults Only 18+', 'Early Childhood', 'Everyone', 'Everyone 10+', 'G',
                       'Kids to Adults', 'M', 'MA15+', 'Mature 17+', 'PG', 'R18+', 'Rating Pending', 'Teen', 'X18+')}
LABELS = {'Number': 'Issue number', 'Count': 'Issues in series', 'AlternateCount': 'Alternate issue count',
          'LanguageISO': 'Language code', 'Penciller': 'Penciller', 'CoverArtist': 'Cover artist',
          'BlackAndWhite': 'Black and white', 'ScanInformation': 'Scan information', 'SeriesGroup': 'Series group',
          'MainCharacterOrTeam': 'Main character or team', 'AgeRating': 'Age rating', 'StoryArc': 'Story arc',
          'AlternateSeries': 'Alternate series', 'AlternateNumber': 'Alternate issue number', 'CommunityRating': 'Community rating'}


def parse_xml(text):
    if len(text.encode('utf-8')) > MAX_XML_BYTES or '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise ValueError('ComicInfo.xml is oversized or contains unsupported XML declarations.')
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    try:
        root = ET.fromstring(text, parser=parser)
    except ET.ParseError as exc:
        raise ValueError('ComicInfo.xml is not valid XML.') from exc
    if root.tag != 'ComicInfo':
        raise ValueError('Metadata must have a ComicInfo root element.')
    return root


def read_folder_metadata(folder):
    files = [p for p in Path(folder).iterdir() if p.is_file() and p.name.casefold() == 'comicinfo.xml']
    if len(files) > 1:
        raise ValueError('More than one ComicInfo.xml was found.')
    if not files:
        return None
    if files[0].stat().st_size > MAX_XML_BYTES:
        raise ValueError("ComicInfo.xml is too large.")
    data = files[0].read_bytes()
    if len(data) > MAX_XML_BYTES:
        raise ValueError('ComicInfo.xml is too large.')
    # Parse bytes first so an existing XML encoding declaration is respected.
    if b'<!doctype' in data.replace(b'\x00', b'').lower() or b'<!entity' in data.replace(b'\x00', b'').lower():
        raise ValueError('Unsupported ComicInfo XML declarations.')
    try:
        root = ET.fromstring(data, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True)))
    except ET.ParseError as exc:
        raise ValueError('ComicInfo.xml is not valid XML.') from exc
    text = ET.tostring(root, encoding='unicode')
    parse_xml(text)
    return text


def validate_field(name, value):
    if not value.strip():
        raise ValueError(f'Enter a value for {LABELS.get(name, name)} or uncheck it.')
    if any(not (c in '\t\r\n' or 0x20 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF) for c in value):
        raise ValueError('Metadata contains an unsupported control character.')
    if name in INTEGER:
        if not re.fullmatch(r'[+-]?[0-9]+', value.strip()):
            raise ValueError(f'{name} must be a whole number.')
        number = int(value)
        if not -(2**31) <= number < 2**31:
            raise ValueError(f'{name} is outside the supported range.')
        if name in ('Month', 'Day') and not 1 <= number <= (12 if name == 'Month' else 31):
            raise ValueError(f'Enter a valid {name.lower()}.')
    if name in ENUMS and value not in ENUMS[name]:
        raise ValueError(f'Choose a supported {LABELS.get(name, name)} value.')
    if name == 'CommunityRating':
        if not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)', value.strip()):
            raise ValueError('Enter a decimal community rating, such as 4.5.')
        try:
            number = Decimal(value)
            if not number.is_finite() or not 0 <= number <= 5 or number.as_tuple().exponent < -2:
                raise ValueError('Community rating must be 0–5 with at most two decimal places.')
        except InvalidOperation as exc:
            raise ValueError('Enter a numeric community rating.') from exc


def edit_xml(original, values):
    root = parse_xml(original) if original else ET.Element('ComicInfo')
    for name, value in values.items():
        if name not in FIELDS or name in ('Pages', 'PageCount'):
            raise ValueError('Unsupported editable metadata field.')
        if value is not None:
            validate_field(name, value)
        for node in list(root):
            if node.tag == name:
                root.remove(node)
        if value is not None:
            ET.SubElement(root, name).text = value
    # Known fields must follow the XSD sequence; preserve unknown nodes as well.
    ordered = sorted(list(root), key=lambda n: FIELDS.index(n.tag) if n.tag in FIELDS else len(FIELDS))
    root[:] = ordered
    return ET.tostring(root, encoding='unicode')


def export_xml(text, plan, entries, image_paths):
    root = parse_xml(text)
    count = root.find('PageCount')
    if count is None:
        count = ET.SubElement(root, 'PageCount')
    count.text = str(len(entries))
    mapping = {}
    n = len(plan.scan.pages)
    for output, (left, right) in enumerate(entries):
        for source in (left,) if right is None else (left, right):
            mapping[n-1-source if plan.scan.right_to_left else source] = output
    pages = root.find('Pages')
    if pages is not None:
        groups = {}
        for node in list(pages):
            if node.tag != 'Page':
                continue
            try:
                output = mapping.get(int(node.get('Image', '')))
            except ValueError:
                output = None
            pages.remove(node)
            if output is None:
                continue
            if output not in groups:
                groups[output] = copy.deepcopy(node)
            else:
                dest = groups[output]
                for key, value in node.attrib.items():
                    if key == 'Type':
                        dest.set(key, ' '.join(dict.fromkeys((dest.get(key, '')+' '+value).split())))
                    elif key not in ('Image', 'ImageSize', 'ImageWidth', 'ImageHeight', 'DoublePage'):
                        dest.set(key, value if not dest.get(key) or dest.get(key) == value else dest.get(key)+' / '+value)
                dest.extend(copy.deepcopy(list(node)))
        from PIL import Image
        for output, node in sorted(groups.items()):
            node.set('Image', str(output))  # Always zero-based; independent of filename numbering.
            path = image_paths[output]
            with Image.open(path) as image:
                node.set('ImageWidth', str(image.width)); node.set('ImageHeight', str(image.height))
            node.set('ImageSize', str(path.stat().st_size))
            if entries[output][1] is not None:
                node.set('DoublePage', 'true')
            pages.append(node)
    root[:] = sorted(list(root), key=lambda node: FIELDS.index(node.tag) if node.tag in FIELDS else len(FIELDS))
    ET.indent(root)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)
