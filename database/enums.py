"""Controlled vocabularies shared by models, schemas and services."""
from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value

    @classmethod
    def values(cls) -> list[str]:
        return [m.value for m in cls]


class PropertyType(StrEnum):
    DETACHED = "Detached"
    SEMI_DETACHED = "Semi-Detached"
    LINK = "Link"
    FREEHOLD_TOWNHOUSE = "Freehold Townhouse"
    CONDO_TOWNHOUSE = "Condo Townhouse"


class ListingStatus(StrEnum):
    FOR_SALE = "For Sale"
    SOLD = "Sold"
    SUSPENDED = "Suspended"
    TERMINATED = "Terminated"
    EXPIRED = "Expired"
    UNKNOWN = "Unknown"

class MortgageType(StrEnum):
    FIXED = "Fixed"
    VARIABLE = "Variable"


class Compounding(StrEnum):
    SEMI_ANNUAL = "semi_annual"
    MONTHLY = "monthly"


class DownPaymentBasis(StrEnum):
    AMOUNT = "amount"
    PERCENT = "percent"

class IngestionMethod(StrEnum):
    MANUAL_PASTE = "manual_paste"
    UPLOADED_PAGE = "uploaded_page"
    MANUAL_ENTRY = "manual_entry"


class ExtractionStatus(StrEnum):
    PENDING = "pending"
    EXTRACTED = "extracted"
    VERIFIED = "verified"
    FAILED = "failed"


class LogStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    REJECTED = "rejected"


class TermsStatus(StrEnum):
    UNREVIEWED = "unreviewed"
    NOT_PERMITTED = "automated_retrieval_not_permitted"
    PERMITTED = "automated_retrieval_permitted"
