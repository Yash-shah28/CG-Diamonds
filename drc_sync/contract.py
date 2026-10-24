"""DRC request preset based on the supplied curl example and accepted import headers."""
import copy
import re

ENDPOINT = 'https://connect.drc.diamonds/v1/inventory/push'
OLD_ENDPOINT = 'https://connect.drc.diamonds/v1/v1/inventory/push'
FORMAT = 'drc-headers-v1'
# The first column is the outgoing key: exact curl keys for its six fields,
# otherwise an accepted source header from the user's DRC import field list.
HEADER_SPEC = '''StockId|Stock ID;Stock #;Stock No;Stock Number;Packet No
Diamond Type|Diamond Type;Natural/Lab
Shape|Shape;Cut Shape
Weight|Weight;Cts;Carat;Carats;Carat Weight
Color|Color;Colour
Clarity|Clarity;Clr
Cut|Cut;Cut Grade
Polish|Polish;Pol
Symmetry|Symmetry;Sym
Fluorescence|Fluorescence;FL Intensity;Flr;Fluorescence Intensity
Fluorescence Color|Fluorescence Color;FL Color
Lot #|Lot #;Lot No;Lot;Lot Number;Batch
Fancy Color|Fancy Color;F Color
Fancy Intensity|Fancy Intensity;F Intensity;Fancy Color Intensity
Overtone|Overtone;F Overtone;Fancy Color Overtone
PricePerCarat|Price Per Carat;Price/Ct;Price Per Ct;Rate;PPC;Rate Per Carat
Final Price|Total Price;Amount;Final Price
Cost Rate|Cost Rate;Cost/Ct;Buy Rate;Purchase Rate
Cost Price|Cost Price;Total Cost;Buy Price;Purchase Price
Discount|Discount;Disc %;Back;Rapaport Trade Discount %
Lab|Lab;Certificate Lab
Certificate #|Certificate #;Cert No;Report No;Report #;Certificate Number
Certificate Comment|Certificate Comment;Report Comment;Cert comment
Certificate Image|Certificate Image;Cert Link;Cert URL
Laser Inscription|Laser Inscription;Inscription
Video|Video;Video Link;360 Video;Diamond Video;VideoURL
Image 1|Image;Image 1;Photo;Diamond Image 1
Image 2|Image 2;Photo 2;Diamond Image 2
Image 3|Image 3;Photo 3;Diamond Image 3
Image 4|Image 4;Photo 4;Diamond Image 4
Image 5|Image 5;Photo 5;Diamond Image 5
Image 6|Image 6;Photo 6;Diamond Image 6
Measurements|Measurements;Size;L x W x D
Depth (mm)|Depth (mm);H;Height;Measurement Depth
Length|Length;L;Measurement Length
Width|Width;W;Measurement Width
L/W Ratio|L/W Ratio;Ratio
Table|Table;Table %
Depth %|Depth %;Total Depth
Crown Angle|Crown Angle;C.Angle
Crown Height|Crown Height;C.Height
Pavilion Angle|Pavilion Angle;P.Angle
Pavilion Depth|Pavilion Depth;P.Depth
Girdle %|Girdle %;Girdle Percent
Girdle|Girdle;Girdle Condition
Girdle Thick|Girdle Thick;GT
Girdle Thin|Girdle Thin;GN
Culet|Culet;Culet Size
Culet Condition|Culet Condition
BGM|BGM;Brown/Green/Milky
Tinge|Tinge;Shade
Milky|Milky;Milkiness
Eye Clean|Eye Clean;EC
H&A|H&A;Heart & Arrow
Black Inclusion|Black Inclusion;Black Table;Black Side
Key to Symbols|Key to Symbols;KTS
City|City;Location City
State|State
Country|Country;Origin Country
Status|Status;Availability
Comment|Comment;Remarks;Member Comment
Growth Type|Growth Type;Type;CVD/HPHT
Star Length|Star Length;Star %
Treatment|Treatment;Enhancement
Country of Origin|Country of Origin;Origin
Country of Polishing|Country of Polishing;Polished In
Category|Category'''
ALIASES = {key: tuple(aliases.split(';')) for key, aliases in (line.split('|') for line in HEADER_SPEC.splitlines())}
FIELDS = tuple(ALIASES)
SAMPLE_FIELDS = ('StockId', 'Shape', 'Weight', 'Color', 'Clarity', 'PricePerCarat')
REQUIRED_FIELDS = ('StockId', 'Shape', 'Weight', 'PricePerCarat')
LEGACY = dict(zip(('stock_id', 'shape', 'carat', 'color', 'clarity', 'price_per_carat'), SAMPLE_FIELDS))
DECIMAL_FIELDS = ('Final Price', 'Cost Rate', 'Cost Price', 'Discount', 'Depth (mm)', 'Length', 'Width',
                  'L/W Ratio', 'Table', 'Depth %', 'Crown Angle', 'Crown Height', 'Pavilion Angle',
                  'Pavilion Depth', 'Girdle %', 'Star Length')

def suggested_field(header):
    normalize = lambda value: re.sub(r'[^a-z0-9]', '', str(value).lower().replace('%', 'percent').replace('#', 'number'))
    for field, aliases in ALIASES.items():
        if normalize(header) in {normalize(alias) for alias in (field,) + aliases}:
            return field
    return ''

def new_profile():
    return {'id':'natural-main', 'kind':'Natural', 'path':'', 'sheet':'Sheet1',
            'enabled':True, 'endpoint':ENDPOINT, 'request_format':FORMAT,
            'mapping':{'Stock No':'StockId', 'Shape':'Shape', 'Weight':'Weight',
                       'Color':'Color', 'Clarity':'Clarity', 'Price Per Carat':'PricePerCarat'},
            'required':list(REQUIRED_FIELDS), 'numeric':['Weight','PricePerCarat'],
            'decimal_fields':list(DECIMAL_FIELDS), 'string_fields':list(FIELDS),
            'identity':'StockId', 'omit_blank':True,
            'confirmed':False, 'retry_safe':False, 'success_field':'', 'success_value':True}

def migrate_profile(profile):
    migrated = copy.deepcopy(profile)
    if migrated.get('request_format') == FORMAT:
        return migrated
    mapping = migrated.get('mapping', {})
    legacy = any(field in LEGACY for field in mapping.values())
    old_endpoint = migrated.get('endpoint') == OLD_ENDPOINT
    if not legacy and not old_endpoint and migrated.get('endpoint', ENDPOINT) != ENDPOINT:
        return migrated
    if old_endpoint:
        migrated['endpoint'] = ENDPOINT
    if legacy:
        migrated['mapping'] = {column:LEGACY.get(field, field) for column, field in mapping.items()}
        for setting in ('required', 'numeric', 'string_fields'):
            migrated[setting] = [LEGACY.get(field, field) for field in migrated.get(setting, [])]
        migrated['identity'] = LEGACY.get(migrated.get('identity'), migrated.get('identity', ''))
    migrated['request_format'] = FORMAT
    migrated['string_fields'] = sorted(set(migrated.get('string_fields', [])) | set(FIELDS))
    migrated['decimal_fields'] = list(DECIMAL_FIELDS)
    migrated['omit_blank'] = True
    migrated['confirmed'] = False
    migrated['retry_safe'] = False
    return migrated
