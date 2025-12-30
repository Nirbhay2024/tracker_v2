import textwrap
import openpyxl
import csv
from PIL import Image, ImageDraw, ImageFont, ExifTags, ImageOps
from io import BytesIO
from django.core.files.base import ContentFile
from django.contrib.staticfiles import finders
from geopy.geocoders import Nominatim
import datetime

# ==========================================
# 1. ADDRESS LOOKUP (Reverse Geocoding)
# ==========================================
def get_address_from_coords(lat, lon):
    if not lat or not lon:
        return "Address Unavailable"
    try:
        # User-agent is required by Nominatim
        geolocator = Nominatim(user_agent="tracker_app_v1")
        location = geolocator.reverse((float(lat), float(lon)), exactly_one=True, timeout=5)
        if location:
            return location.address
    except Exception as e:
        print(f"DEBUG: Geocoding Failed: {e}")
    return "Location Unknown"

# ==========================================
# 2. GPS EXTRACTION
# ==========================================
def _convert_to_degrees(value):
    d = float(value[0])
    m = float(value[1])
    s = float(value[2])
    return d + (m / 60.0) + (s / 3600.0)

def get_gps_from_image(image_field):
    try:
        img = Image.open(image_field)
        exif_data = img._getexif()
        if not exif_data: return None, None
        
        gps_info = exif_data.get(34853)
        if not gps_info: return None, None

        lat_gps = gps_info.get(2)
        lat_ref = gps_info.get(1)
        lon_gps = gps_info.get(4)
        lon_ref = gps_info.get(3)

        if lat_gps and lat_ref and lon_gps and lon_ref:
            lat = _convert_to_degrees(lat_gps)
            if lat_ref != 'N': lat = -lat
            lon = _convert_to_degrees(lon_gps)
            if lon_ref != 'E': lon = -lon
            return f"{lat:.6f}", f"{lon:.6f}"
    except Exception:
        pass
    return None, None

# ==========================================
# 3. WATERMARKING LOGIC (Advanced)
# ==========================================
def watermark_image(image_field, lat, lon):
    try:
        print("DEBUG: Processing Image for Watermark...")
        
        COMPANY_NAME = "Nexsafe"
        
        if lat and lon:
            address_text = get_address_from_coords(lat, lon)
            gps_text = f"Lat: {float(lat):.6f}, Lon: {float(lon):.6f}"
        else:
            address_text = "Location Not Captured"
            gps_text = "GPS Unavailable"

        # Load Image
        img = Image.open(image_field)
        img = ImageOps.exif_transpose(img) # Fix rotation
        img = img.convert("RGBA") # RGBA for transparency
        draw = ImageDraw.Draw(img)
        W, H = img.size

        # Fonts
        try:
            # Scale font relative to image height
            font_title = ImageFont.truetype("arial.ttf", int(H * 0.035))
            font_body = ImageFont.truetype("arial.ttf", int(H * 0.025))
        except OSError:
            font_title = ImageFont.load_default()
            font_body = ImageFont.load_default()

        # Wrap Address
        wrapped_address = "\n".join(textwrap.wrap(address_text, width=45))

        # Load Logo
        logo_path = finders.find('tracker/logo.png')
        logo = None
        if logo_path:
            try:
                logo = Image.open(logo_path).convert("RGBA")
                logo_size = int(H * 0.10)
                logo.thumbnail((logo_size, logo_size))
            except Exception: pass

        # Measure Text
        def get_text_size(text, font):
            if hasattr(draw, "textbbox"):
                bbox = draw.textbbox((0, 0), text, font=font)
                return bbox[2], bbox[3]
            else:
                return draw.textsize(text, font=font)

        w_t, h_t = get_text_size(COMPANY_NAME, font_title)
        w_g, h_g = get_text_size(gps_text, font_body)
        w_a, h_a = get_text_size(wrapped_address, font_body)

        text_width = max(w_t, w_g, w_a)
        text_height = h_t + h_g + h_a + 25 

        # Box Dimensions
        PADDING = 30
        logo_w = logo.size[0] if logo else 0
        logo_h = logo.size[1] if logo else 0
        
        box_width = logo_w + text_width + (PADDING * 3)
        box_height = max(text_height, logo_h) + (PADDING * 2)

        # Placement (Bottom Right)
        x2 = W - PADDING
        y2 = H - PADDING
        x1 = x2 - box_width
        y1 = y2 - box_height

        # Draw Semi-Transparent Box
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rectangle([x1, y1, x2, y2], fill=(0, 0, 0, 180)) # Black 180 alpha
        
        img = Image.alpha_composite(img, overlay)
        draw = ImageDraw.Draw(img)

        # Draw Content
        current_x = x1 + PADDING
        current_y = y1 + PADDING

        if logo:
            logo_y = y1 + (box_height - logo_h) // 2
            img.paste(logo, (int(current_x), int(logo_y)), logo)
            current_x += logo_w + PADDING

        draw.text((current_x, current_y), COMPANY_NAME, fill="white", font=font_title)
        current_y += h_t + 5
        draw.text((current_x, current_y), gps_text, fill="#d0d0d0", font=font_body)
        current_y += h_g + 5
        draw.text((current_x, current_y), wrapped_address, fill="#b0b0b0", font=font_body)

        # Save as JPEG (Convert back to RGB)
        buffer = BytesIO()
        img.convert("RGB").save(buffer, format='JPEG', quality=95)
        return ContentFile(buffer.getvalue())

    except Exception as e:
        print(f"DEBUG: Watermark Logic Crashed: {e}")
        image_field.seek(0)
        return image_field

# ==========================================
# 4. EXCEL HELPERS
# ==========================================
def get_file_headers(file_field):
    if not file_field: return []
    try:
        try: file_field.open('rb')
        except: pass
        file_field.seek(0)
        filename = file_field.name.lower()
        if filename.endswith('.xlsx'):
            workbook = openpyxl.load_workbook(file_field, data_only=True)
            sheet = workbook.active
            return [str(cell.value).strip() for cell in sheet[1] if cell.value]
        else:
            decoded = file_field.read().decode('utf-8-sig').splitlines()
            reader = csv.reader(decoded)
            return next(reader, [])
    except: return []

def get_dropdown_options(file_field, column_name):
    if not file_field: return []
    options = set()
    try:
        try: file_field.open('rb')
        except: pass
        file_field.seek(0)
        filename = file_field.name.lower()
        if filename.endswith('.xlsx'):
            workbook = openpyxl.load_workbook(file_field, data_only=True)
            sheet = workbook.active
            headers = [str(cell.value).strip() if cell.value else '' for cell in sheet[1]]
            try:
                idx = headers.index(column_name.strip())
                for row in sheet.iter_rows(min_row=2, values_only=True):
                    if row[idx]: options.add(str(row[idx]).strip())
            except ValueError: pass
        else:
            decoded = file_field.read().decode('utf-8-sig').splitlines()
            reader = csv.DictReader(decoded)
            reader.fieldnames = [name.strip() for name in reader.fieldnames]
            if column_name.strip() in reader.fieldnames:
                for row in reader:
                    if row.get(column_name.strip()): options.add(row.get(column_name.strip()).strip())
        return [(o, o) for o in sorted(list(options))]
    except: return []