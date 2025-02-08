import streamlit as st
import fitz  # PyMuPDF
import easyocr
import zipfile
from io import BytesIO
import tempfile
import os
import logging
from PIL import Image
import io

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
MAX_PDF_SIZE_MB = 50
MAX_IMAGE_SIZE_MB = 10
MAX_PAGE_PIXELS = 1e7  # ~10000x1000 pixels
OCR_TIMEOUT_SECONDS = 30

# Initialize EasyOCR reader once
@st.cache_resource
def get_reader():
    try:
        return easyocr.Reader(['en'])
    except Exception as e:
        st.error(f"Failed to initialize EasyOCR: {str(e)}")
        return None

reader = get_reader()

def validate_pdf_size(pdf_bytes):
    """Validate PDF file size"""
    size_mb = len(pdf_bytes) / (1024 * 1024)
    if size_mb > MAX_PDF_SIZE_MB:
        raise ValueError(f"PDF file too large (max {MAX_PDF_SIZE_MB}MB allowed, got {size_mb:.1f}MB)")

def validate_image(image_bytes):
    """Validate image size and format"""
    if len(image_bytes) > MAX_IMAGE_SIZE_MB * 1024 * 1024:
        raise ValueError(f"Image too large (max {MAX_IMAGE_SIZE_MB}MB allowed)")
    try:
        img = Image.open(io.BytesIO(image_bytes))
        return img.format in ['PNG', 'JPEG', 'TIFF']
    except Exception:
        return False

def process_pdf(pdf_bytes):
    """Process PDF and return markdown content with OCR results"""
    try:
        validate_pdf_size(pdf_bytes)
        doc = fitz.open(stream=pdf_bytes)
    except Exception as e:
        error_msg = f"Failed to open PDF: {str(e)}"
        logger.error(error_msg)
        raise ValueError(error_msg)

    markdown = []
    errors = []
    warnings = []
    
    # Add progress tracking
    progress_bar = st.progress(0)
    status_text = st.empty()
    total_pages = len(doc)
    
    try:
        for page_num in range(total_pages):
            progress = (page_num + 1) / total_pages
            progress_bar.progress(progress)
            status_text.text(f"Processing page {page_num + 1}/{total_pages}")
            
            try:
                page = doc.load_page(page_num)
                
                # Check page size
                if page.rect.width * page.rect.height > MAX_PAGE_PIXELS:
                    warnings.append(f"⚠️ Page {page_num + 1} too large, might affect processing quality")
                
                # Extract text
                try:
                    text = page.get_text()
                    markdown.append(f"## Page {page_num + 1}\n\n{text}")
                except Exception as e:
                    errors.append(f"❌ Failed to extract text from page {page_num + 1}: {str(e)}")
                    continue
                
                # Process images
                img_list = page.get_images(full=True)
                if img_list:
                    markdown.append(f"### Images on Page {page_num + 1}")
                
                for img_idx, img in enumerate(img_list):
                    try:
                        xref = img[0]
                        base_image = doc.extract_image(xref)
                        image_bytes = base_image["image"]
                        
                        # Validate image
                        if not validate_image(image_bytes):
                            warnings.append(f"⚠️ Skipped image {img_idx + 1} on page {page_num + 1}: Invalid format or corrupted")
                            continue
                        
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f:
                            f.write(image_bytes)
                            temp_path = f.name
                        
                        try:
                            if reader is None:
                                raise ValueError("OCR engine not initialized")
                            
                            ocr_results = reader.readtext(temp_path, detail=0)
                            if ocr_results:
                                ocr_text = " ".join(ocr_results)
                                markdown.append(f"**Image {img_idx + 1} OCR:**\n{ocr_text}\n")
                            else:
                                warnings.append(f"⚠️ No text found in image {img_idx + 1} on page {page_num + 1}")
                        except Exception as e:
                            errors.append(f"❌ OCR Error for image {img_idx + 1} on page {page_num + 1}: {str(e)}")
                        finally:
                            try:
                                os.unlink(temp_path)
                            except Exception:
                                pass
                    except Exception as e:
                        errors.append(f"❌ Failed to process image {img_idx + 1} on page {page_num + 1}: {str(e)}")
            
            except Exception as e:
                errors.append(f"❌ Failed to process page {page_num + 1}: {str(e)}")
    
    finally:
        progress_bar.empty()
        status_text.empty()
        doc.close()
    
    # Add processing summary
    if errors or warnings:
        markdown.append("\n## Processing Summary")
        if errors:
            markdown.append("\n### Errors")
            markdown.extend(errors)
        if warnings:
            markdown.append("\n### Warnings")
            markdown.extend(warnings)
    
    return "\n\n".join(markdown)

# Streamlit UI
st.title("📄 PDF to Markdown Converter with OCR")
st.write("Extract text and OCR images from PDF files to Markdown")

# Add file size warning
st.info(f"Maximum PDF size: {MAX_PDF_SIZE_MB}MB per file")

uploaded_files = st.file_uploader(
    "Upload PDF files", 
    type=["pdf"],
    accept_multiple_files=True
)

if st.button("✨ Process Files") and uploaded_files:
    zip_buffer = BytesIO()
    total_files = len(uploaded_files)
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for i, uploaded_file in enumerate(uploaded_files):
            with st.spinner(f"Processing {uploaded_file.name} ({i+1}/{total_files})..."):
                try:
                    md_content = process_pdf(uploaded_file.getvalue())
                    if md_content:
                        filename = f"{os.path.splitext(uploaded_file.name)[0]}.md"
                        zipf.writestr(filename, md_content.encode('utf-8'))
                except Exception as e:
                    st.error(f"Error processing {uploaded_file.name}: {str(e)}")
                    logger.error(f"Error processing {uploaded_file.name}: {str(e)}", exc_info=True)
    
    if zip_buffer.getvalue():  # Only show download if we have processed files
        st.success("✅ Processing complete!")
        st.download_button(
            label="📥 Download Markdown Files",
            data=zip_buffer.getvalue(),
            file_name="processed_documents.zip",
            mime="application/zip"
        )
