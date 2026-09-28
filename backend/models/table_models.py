from pydantic import BaseModel
from typing import List, Optional, Any

class OCRItem(BaseModel):
    text: str
    confidence: float
    bbox: List[float]  # [x1, y1, x2, y2]
    center_x: float
    center_y: float

class TableCell(BaseModel):
    value: str
    confidence: float = 1.0
    bbox: Optional[List[float]] = None
    is_low_confidence: bool = False

class TableRow(BaseModel):
    cells: List[TableCell]

class ExtractedTable(BaseModel):
    headers: List[str]
    rows: List[List[TableCell]]
    avg_confidence: float
    min_confidence: float
    low_confidence_count: int
    row_count: int
    col_count: int
    data_quality_label: str

class ConversionResponse(BaseModel):
    success: bool
    job_id: str
    filename: str
    pages: int
    rows: int
    columns: int
    confidence: float
    extraction_method: str
    data_quality_label: str
    headers: List[str]
    table_data: List[List[str]]
    low_conf_cells: List[List[bool]]
    download_url: str
