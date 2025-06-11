from flask import Flask, request, send_file, jsonify, render_template
import io
import os
import requests
import re
import uuid
import zipfile
from PIL import Image

app = Flask(__name__)

# Configuration
UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'compressed'
ZIP_FOLDER = 'zips'
MAX_SIZE = 100352  # 98 KB
DEBUG = True

# Ensure folders exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
os.makedirs(ZIP_FOLDER, exist_ok=True)

def compress_image(image, output_path, max_size=MAX_SIZE):
    try:
        output_path = os.path.abspath(output_path)
        output_dir = os.path.dirname(output_path)
        os.makedirs(output_dir, exist_ok=True)

        if DEBUG:
            print(f"Attempting to save to: {output_path}")

        # Resize image if width exceeds 1200 pixels
        max_width = 1200
        if image.width > max_width:
            ratio = max_width / image.width
            new_height = int(image.height * ratio)
            image = image.resize((max_width, new_height), Image.LANCZOS)
            if DEBUG:
                print(f"Resized image to width {max_width}x{new_height}")

        # Convert to RGB for JPEG compatibility (handles PNG and other formats)
        if image.mode != 'RGB':
            if DEBUG and image.format == 'PNG':
                print(f"Converting PNG to JPG")
            image = image.convert('RGB')

        # Force JPEG format
        saved_format = 'JPEG'
        final_quality = None

        # Check initial size with high quality
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=95, optimize=True, progressive=True)

        if buffer.tell() <= max_size:
            image.save(output_path, format='JPEG', quality=95, optimize=True, progressive=True)
            final_quality = 95
        else:
            # Progressively reduce JPEG quality
            for quality in range(95, 29, -5):
                buffer = io.BytesIO()
                image.save(buffer, format='JPEG', quality=quality, optimize=True, progressive=True)
                size = buffer.tell()
                if size <= max_size:
                    image.save(output_path, format='JPEG', quality=quality, optimize=True, progressive=True)
                    final_quality = quality
                    break
            else:
                # Save at minimum quality and check size
                image.save(output_path, format='JPEG', quality=30, optimize=True, progressive=True)
                compressed_size = os.path.getsize(output_path)
                if compressed_size > max_size:
                    # Further resize if still too large
                    factor = 1
                    while compressed_size > max_size:
                        current_width = int(image.width / factor)
                        current_height = int(image.height / factor)
                        if current_width < 1 or current_height < 1:
                            break
                        resized_image = image.resize((current_width, current_height), Image.LANCZOS)
                        buffer = io.BytesIO()
                        resized_image.save(buffer, format='JPEG', quality=30, optimize=True, progressive=True)
                        size = buffer.tell()
                        if size <= max_size:
                            resized_image.save(output_path, format='JPEG', quality=30, optimize=True, progressive=True)
                            compressed_size = os.path.getsize(output_path)
                            final_quality = 30
                            break
                        factor *= 2
                    else:
                        if 'resized_image' in locals() and current_width > 0 and current_height > 0:
                            resized_image.save(output_path, format='JPEG', quality=30, optimize=True, progressive=True)
                        else:
                            image.save(output_path, format='JPEG', quality=30, optimize=True, progressive=True)
                        compressed_size = os.path.getsize(output_path)
                        if compressed_size > max_size:
                            raise IOError(f"Could not compress JPEG image to {max_size} bytes (final size: {compressed_size} bytes) at minimum quality 30")
                        final_quality = 30

        if not os.path.exists(output_path):
            raise IOError(f"Failed to save file: {output_path}")

        compressed_size = os.path.getsize(output_path)
        final_path = output_path

        try:
            with Image.open(final_path) as test_image:
                test_image.verify()
        except Exception as e:
            raise IOError(f"Saved image is corrupted: {final_path}, Error: {str(e)}")

        return final_path, compressed_size, final_quality

    except (PermissionError, OSError) as e:
        raise IOError(f"Error saving image to {output_path}: {str(e)}")

@app.route('/')
def index():
    print(f"Current working directory: {os.getcwd()}")
    print(f"Template path checked: {os.path.abspath('templates/index.html')}")
    try:
        if not os.path.exists('templates/index.html'):
            return jsonify({'error': 'Template file not found at templates/index.html'}), 500
        return render_template('index.html')
    except Exception as e:
        return jsonify({'error': f"Template rendering failed: {str(e)}"}), 500

@app.route('/compress', methods=['POST'])
def compress():
    try:
        images = []
        names = []
        source_description = "image"

        if 'files[]' in request.files:
            files = request.files.getlist('files[]')
            source_description = "local image"
            names = request.form.getlist('names[]')
            while len(names) < len(files):
                names.append("")

            for file, name in zip(files, names):
                if file.filename == '':
                    continue
                ext = os.path.splitext(file.filename)[1].lower()
                if ext not in ['.jpg', '.jpeg', '.png', '.gif', '.bmp']:
                    continue
                try:
                    image = Image.open(file)
                    if image.format == 'PNG':
                        if DEBUG:
                            print(f"Converting local PNG to JPG: {file.filename}")
                        image = image.convert('RGB')
                    elif image.format not in ['JPEG', 'PNG', 'GIF', 'BMP']:
                        image = image.convert('RGB')
                    images.append(image)
                    names.append(name or f"image_{len(images)}")
                except Exception as e:
                    if DEBUG:
                        print(f"Error loading local file {file.filename}: {str(e)}")
                    continue
        elif 'urls[]' in request.form:
            urls = request.form.getlist('urls[]')
            names = request.form.getlist('names[]')
            while len(names) < len(urls):
                names.append("")
            source_description = "image from URL"

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
                'Accept': 'image/jpeg,image/png,image/webp,image/*,*/*;q=0.8'
            }
            fallback_headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15',
                'Accept': 'image/*,*/*;q=0.8'
            }

            for url, name in zip(urls, names):
                if not url.strip():
                    continue
                try:
                    response = requests.get(url, headers=headers, timeout=10)
                    response.raise_for_status()
                    content_type = response.headers.get('content-type', '')
                    if not content_type.startswith('image/'):
                        if DEBUG:
                            print(f"Invalid content-type for {url}: {content_type}")
                        continue
                    image = Image.open(io.BytesIO(response.content))
                    if image.format == 'PNG':
                        if DEBUG:
                            print(f"Converting downloaded PNG to JPG: {url}")
                        image = image.convert('RGB')
                    elif image.format not in ['JPEG', 'PNG', 'GIF', 'BMP']:
                        image = image.convert('RGB')
                    images.append(image)
                    names.append(name or f"image_{len(images)}")
                except Exception as e:
                    if DEBUG:
                        print(f"Error downloading {url}: {str(e)}")
                    continue

        if not images:
            return jsonify({'error': 'No valid images to process'}), 400

        results = []
        used_filenames = set()
        total_images = len(images)
        successful_images = []  # Track successfully compressed images for ZIP

        for index, (image, custom_name) in enumerate(zip(images, names), 1):
            try:
                custom_name = re.sub(r'[<>:"/\\|?*]', '', custom_name)
                if not custom_name:
                    custom_name = f"image_{index}"

                ext = '.jpg'
                base_name = custom_name
                output_filename = f"{base_name}_compressed{ext}"
                output_path = os.path.join(OUTPUT_FOLDER, output_filename)

                counter = 1
                while output_filename.lower() in used_filenames or os.path.exists(output_path):
                    output_filename = f"{base_name}_compressed_{counter}{ext}"
                    output_path = os.path.join(OUTPUT_FOLDER, output_filename)
                    counter += 1
                used_filenames.add(output_filename.lower())

                final_path, size, final_quality = compress_image(image, output_path)
                if os.path.exists(final_path):
                    status_message = f"Compressed {os.path.basename(final_path)} ({size} bytes)"
                    if final_quality is not None and final_quality < 50:
                        status_message += f" (Warning: Low quality {final_quality} used)"
                    result = {
                        'filename': os.path.basename(final_path),
                        'size': size,
                        'path': final_path,
                        'message': status_message
                    }
                    results.append(result)
                    successful_images.append(result)  # Add to ZIP candidates
                    if DEBUG:
                        print(f"Success: File saved to {final_path}")
                else:
                    results.append({'filename': custom_name, 'error': f"Failed to save {output_filename}"})
                    if DEBUG:
                        print(f"Failure: File not found at {final_path}")

            except Exception as e:
                results.append({'filename': custom_name, 'error': f"Error processing {source_description} {index}: {str(e)}"})
                if DEBUG:
                    print(f"Error: {str(e)}")

        # Create ZIP file if more than one image was successfully compressed
        zip_filename = None
        if len(successful_images) > 1:
            try:
                zip_name = f"compressed_images_{uuid.uuid4().hex}.zip"
                zip_path = os.path.join(ZIP_FOLDER, zip_name)
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for image in successful_images:
                        zipf.write(image['path'], image['filename'])
                if os.path.exists(zip_path):
                    zip_filename = zip_name
                    if DEBUG:
                        print(f"Created ZIP file: {zip_path}")
            except Exception as e:
                if DEBUG:
                    print(f"Error creating ZIP file: {str(e)}")
                zip_filename = None

        return jsonify({'results': results, 'zip_filename': zip_filename})

    except Exception as e:
        if DEBUG:
            print(f"Server error: {str(e)}")
        return jsonify({'error': f"Server error: {str(e)}"}), 500

@app.route('/download/<filename>')
def download(filename):
    file_path = os.path.join(OUTPUT_FOLDER, filename)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return jsonify({'error': 'File not found'}), 404

@app.route('/download_zip/<zip_filename>')
def download_zip(zip_filename):
    file_path = os.path.join(ZIP_FOLDER, zip_filename)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True, download_name='compressed_images.zip')
    return jsonify({'error': 'ZIP file not found'}), 404

if __name__ == '__main__':
    app.run(debug=DEBUG)