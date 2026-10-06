from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any

class PatientInfo(BaseModel):
    patient_name: str = ""
    date_of_birth: str = ""
    age: str = ""
    gender: str = ""
    account_number: str = ""
    patient_id: str = ""
    visit_number: str = ""
    address: str = ""
    phone: str = ""
    insurance: str = ""
    pcp: str = ""

class MedicalDocumentRecord(BaseModel):
    source_file: str
    patient_info: PatientInfo = Field(default_factory=PatientInfo)
    assessments: List[str] = Field(default_factory=list)
    visit_code: str = ""
    confidence: Dict[str, float] = Field(default_factory=dict)
    source_pages: Dict[str, int] = Field(default_factory=dict)
    review_required: bool = False
    success: bool = True
    error: Optional[str] = None

class BatchConversionResponse(BaseModel):
    success: bool
    batch_id: str
    total_files: int
    status: str
    poll_url: str

class BatchStatusResponse(BaseModel):
    success: bool
    batch_id: str
    status: str  # 'queued', 'processing', 'completed', 'failed'
    total_files: int
    completed_files: int
    failed_files: int
    current_file: Optional[str] = None
    progress: int
    stage: str
    download_ready: bool = False
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

class BatchExportRequest(BaseModel):
    headers: List[str]
    table_data: List[List[str]]
