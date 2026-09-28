import re

def clean_text(text: str) -> str:
    """Clean and normalize OCR extracted text string."""
    if not text:
        return ""
    # Strip leading and trailing whitespace
    text = text.strip()
    # Replace multiple spaces with a single space
    text = re.sub(r'[ \t]+', ' ', text)
    return text

def is_low_confidence(confidence: float, threshold: float = 0.85) -> bool:
    """Check if an OCR confidence value is below the acceptable threshold."""
    return confidence < threshold

def format_column_header(header: str) -> str:
    """Format and normalize standard column headers."""
    header_clean = clean_text(header).lower()
    
    mapping = {
        'overreader': 'Overreader ID',
        'overreader id': 'Overreader ID',
        'patient id': 'Patient ID',
        'patient name': 'Patient Full Name',
        'patient full name': 'Patient Full Name',
        'date of birth': 'Date of Birth',
        'dob': 'Date of Birth',
        'visit number': 'Visit Number',
        'visit no': 'Visit Number',
        'order number': 'Order Number',
        'order no': 'Order Number',
        'acquisition date/time': 'Acquisition Date/Time',
        'acquisition date': 'Acquisition Date/Time',
        'test type': 'Test Type Value',
        'test type value': 'Test Type Value',
        'test reason': 'Test Reason'
    }
    
    for key, canonical in mapping.items():
        if key in header_clean:
            return canonical
            
    return clean_text(header)
