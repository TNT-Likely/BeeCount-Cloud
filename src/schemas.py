from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

MemberRole = Literal["owner", "editor", "viewer"]
MemberStatus = Literal["active", "left"]
SyncAction = Literal["upsert", "delete"]


class AuthRegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=6)
    device_id: str | None = None
    device_name: str | None = None
    platform: str | None = None
    app_version: str | None = None
    os_version: str | None = None
    device_model: str | None = None
    client_type: Literal["app", "web"] = "app"

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or "." not in normalized.split("@")[-1]:
            raise ValueError("Invalid email format")
        return normalized


class AuthLoginRequest(BaseModel):
    email: str
    password: str
    device_id: str | None = None
    device_name: str | None = None
    platform: str | None = None
    app_version: str | None = None
    os_version: str | None = None
    device_model: str | None = None
    client_type: Literal["app", "web"] = "app"

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or "." not in normalized.split("@")[-1]:
            raise ValueError("Invalid email format")
        return normalized


class AuthRefreshRequest(BaseModel):
    refresh_token: str


class AuthLogoutRequest(BaseModel):
    refresh_token: str | None = None


class UserOut(BaseModel):
    id: str
    email: str
    is_admin: bool = False


class UserProfileOut(BaseModel):
    user_id: str
    email: str
    display_name: str | None = None
    avatar_url: str | None = None
    avatar_version: int = 0


class UserProfilePatchRequest(BaseModel):
    display_name: str

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Display name cannot be empty")
        if len(normalized) > 32:
            raise ValueError("Display name too long")
        return normalized


class UserProfileAvatarUploadOut(BaseModel):
    avatar_url: str
    avatar_version: int


class AuthTokenResponse(BaseModel):
    user: UserOut
    access_token: str
    refresh_token: str
    expires_in: int
    device_id: str
    scopes: list[str] = Field(default_factory=list)


class DeviceOut(BaseModel):
    id: str
    name: str
    platform: str
    app_version: str | None = None
    os_version: str | None = None
    device_model: str | None = None
    last_ip: str | None = None
    last_seen_at: datetime
    created_at: datetime
    session_count: int = 1


class SyncChangeIn(BaseModel):
    ledger_id: str
    entity_type: str
    entity_sync_id: str
    action: SyncAction
    payload: dict[str, Any]
    updated_at: datetime


class SyncPushRequest(BaseModel):
    device_id: str
    changes: list[SyncChangeIn]


class SyncPushResponse(BaseModel):
    accepted: int
    rejected: int
    conflict_count: int = 0
    conflict_samples: list[dict[str, Any]] = Field(default_factory=list)
    server_cursor: int
    server_timestamp: datetime


class SyncChangeOut(BaseModel):
    change_id: int
    ledger_id: str
    entity_type: str
    entity_sync_id: str
    action: SyncAction
    payload: dict[str, Any]
    updated_at: datetime
    updated_by_device_id: str | None


class SyncPullResponse(BaseModel):
    changes: list[SyncChangeOut]
    server_cursor: int
    has_more: bool


class SyncFullResponse(BaseModel):
    ledger_id: str
    snapshot: SyncChangeOut | None
    latest_cursor: int


class SyncLedgerOut(BaseModel):
    ledger_id: str
    path: str
    updated_at: datetime | None
    size: int
    metadata: dict[str, Any]
    role: MemberRole


class LedgerMemberOut(BaseModel):
    user_id: str
    user_email: str | None = None
    user_display_name: str | None = None
    user_avatar_url: str | None = None
    user_avatar_version: int | None = None
    role: MemberRole
    status: MemberStatus
    joined_at: datetime
    left_at: datetime | None


class ShareInviteCreateRequest(BaseModel):
    ledger_id: str
    role: Literal["editor", "viewer"] = "editor"
    max_uses: int = Field(default=1, ge=1, le=100)
    expires_in_hours: int = Field(default=72, ge=1, le=720)


class ShareInviteCreateResponse(BaseModel):
    invite_id: str
    invite_code: str
    ledger_id: str
    role: MemberRole
    max_uses: int
    expires_at: datetime


class ShareInviteRevokeRequest(BaseModel):
    invite_id: str


class ShareInviteRevokeResponse(BaseModel):
    invite_id: str
    revoked: bool


class ShareJoinRequest(BaseModel):
    invite_code: str = Field(min_length=6)


class ShareJoinResponse(BaseModel):
    joined: bool
    ledger_id: str
    role: MemberRole


class ShareLeaveRequest(BaseModel):
    ledger_id: str


class ShareLeaveResponse(BaseModel):
    left: bool
    ledger_id: str


class ShareMemberAddRequest(BaseModel):
    ledger_id: str
    member_email: str
    role: Literal["editor", "viewer"] = "editor"

    @field_validator("member_email")
    @classmethod
    def validate_member_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or "." not in normalized.split("@")[-1]:
            raise ValueError("Invalid email format")
        return normalized


class ShareMemberAddResponse(BaseModel):
    result: Literal["created", "reactivated", "updated", "unchanged"]
    ledger_id: str
    user_id: str
    user_email: str
    role: MemberRole
    status: MemberStatus


class ShareMemberRemoveRequest(BaseModel):
    ledger_id: str
    member_email: str

    @field_validator("member_email")
    @classmethod
    def validate_member_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or "." not in normalized.split("@")[-1]:
            raise ValueError("Invalid email format")
        return normalized


class ShareMemberRemoveResponse(BaseModel):
    removed: bool
    ledger_id: str
    user_id: str
    user_email: str
    role: MemberRole
    status: MemberStatus


class ShareMemberRoleRequest(BaseModel):
    ledger_id: str
    user_id: str
    role: Literal["editor", "viewer"]


class ShareMemberRoleResponse(BaseModel):
    updated: bool
    ledger_id: str
    user_id: str
    role: MemberRole


InviteStatus = Literal["active", "revoked", "expired", "exhausted"]
BackupArtifactKind = Literal["db", "snapshot"]


class ShareInviteListItem(BaseModel):
    invite_id: str
    ledger_id: str
    role: Literal["editor", "viewer"]
    max_uses: int | None
    used_count: int
    expires_at: datetime
    revoked_at: datetime | None
    status: InviteStatus
    created_at: datetime


class AdminBackupCreateRequest(BaseModel):
    ledger_id: str
    note: str | None = None


class AdminBackupCreateResponse(BaseModel):
    snapshot_id: str
    ledger_id: str
    created_at: datetime


class AdminBackupRestoreRequest(BaseModel):
    snapshot_id: str
    device_id: str | None = None


class AdminBackupRestoreResponse(BaseModel):
    restored: bool
    ledger_id: str
    change_id: int


class AdminBackupUploadSnapshotRequest(BaseModel):
    ledger_id: str
    payload: dict[str, Any]
    note: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AdminBackupArtifactOut(BaseModel):
    id: str
    ledger_id: str
    kind: BackupArtifactKind
    file_name: str
    content_type: str | None
    checksum: str
    size: int
    created_at: datetime
    created_by: str
    note: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AdminBackupArtifactUploadResponse(AdminBackupArtifactOut):
    snapshot_id: str | None = None


class UserAdminOut(BaseModel):
    id: str
    email: str
    is_admin: bool
    is_enabled: bool
    created_at: datetime
    display_name: str | None = None
    avatar_url: str | None = None
    avatar_version: int = 0


class UserAdminListOut(BaseModel):
    total: int
    items: list[UserAdminOut]


class UserAdminPatchRequest(BaseModel):
    is_admin: bool | None = None
    is_enabled: bool | None = None


class UserAdminCreateRequest(BaseModel):
    email: str
    password: str = Field(min_length=6)
    is_admin: bool = False
    is_enabled: bool = True

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or "." not in normalized.split("@")[-1]:
            raise ValueError("Invalid email format")
        return normalized


class AdminOverviewOut(BaseModel):
    users_total: int
    users_enabled_total: int
    ledgers_total: int
    transactions_total: int
    accounts_total: int
    categories_total: int
    tags_total: int


class ReadLedgerOut(BaseModel):
    ledger_id: str
    ledger_name: str
    currency: str
    transaction_count: int
    income_total: float
    expense_total: float
    balance: float
    exported_at: datetime | None
    updated_at: datetime
    role: MemberRole
    is_shared: bool = False
    member_count: int = 1


class ReadLedgerDetailOut(ReadLedgerOut):
    source_change_id: int


class ReadTransactionOut(BaseModel):
    id: str
    tx_index: int
    tx_type: str
    amount: float
    happened_at: datetime
    note: str | None
    category_name: str | None
    category_kind: str | None
    account_name: str | None
    from_account_name: str | None
    to_account_name: str | None
    category_id: str | None = None
    account_id: str | None = None
    from_account_id: str | None = None
    to_account_id: str | None = None
    tags: str | None
    tags_list: list[str] = Field(default_factory=list)
    tag_ids: list[str] = Field(default_factory=list)
    attachments: list[dict[str, Any]] | None
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None
    created_by_user_id: str | None = None
    created_by_email: str | None = None
    created_by_display_name: str | None = None
    created_by_avatar_url: str | None = None
    created_by_avatar_version: int | None = None


class ReadAccountOut(BaseModel):
    id: str
    name: str
    account_type: str | None
    currency: str | None
    initial_balance: float | None
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None
    created_by_user_id: str | None = None
    created_by_email: str | None = None


class ReadCategoryOut(BaseModel):
    id: str
    name: str
    kind: str
    level: int | None
    sort_order: int | None
    icon: str | None
    icon_type: str | None
    custom_icon_path: str | None = None
    icon_cloud_file_id: str | None = None
    icon_cloud_sha256: str | None = None
    parent_name: str | None
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None
    created_by_user_id: str | None = None
    created_by_email: str | None = None


class ReadTagOut(BaseModel):
    id: str
    name: str
    color: str | None
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None
    created_by_user_id: str | None = None
    created_by_email: str | None = None


class WorkspaceTransactionOut(ReadTransactionOut):
    pass


class WorkspaceTransactionPageOut(BaseModel):
    items: list[WorkspaceTransactionOut] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class WorkspaceAccountOut(ReadAccountOut):
    pass


class WorkspaceCategoryOut(ReadCategoryOut):
    pass


class WorkspaceTagOut(ReadTagOut):
    pass


AnalyticsScope = Literal["month", "year", "all"]
AnalyticsMetric = Literal["expense", "income", "balance"]


class WorkspaceAnalyticsSummaryOut(BaseModel):
    transaction_count: int
    income_total: float
    expense_total: float
    balance: float


class WorkspaceAnalyticsSeriesItemOut(BaseModel):
    bucket: str
    expense: float
    income: float
    balance: float


class WorkspaceAnalyticsCategoryRankOut(BaseModel):
    category_name: str
    total: float
    tx_count: int


class WorkspaceAnalyticsRangeOut(BaseModel):
    scope: AnalyticsScope
    metric: AnalyticsMetric
    period: str | None
    start_at: datetime | None
    end_at: datetime | None


class WorkspaceAnalyticsOut(BaseModel):
    summary: WorkspaceAnalyticsSummaryOut
    series: list[WorkspaceAnalyticsSeriesItemOut] = Field(default_factory=list)
    category_ranks: list[WorkspaceAnalyticsCategoryRankOut] = Field(default_factory=list)
    range: WorkspaceAnalyticsRangeOut


class ReadSummaryOut(BaseModel):
    ledger_id: str
    transaction_count: int
    income_total: float
    expense_total: float
    balance: float
    latest_happened_at: datetime | None


class WriteCommitMeta(BaseModel):
    ledger_id: str
    base_change_id: int
    new_change_id: int
    server_timestamp: datetime
    idempotency_replayed: bool = False
    entity_id: str | None = None


class WriteBaseRequest(BaseModel):
    base_change_id: int = Field(ge=0)
    request_id: str | None = Field(default=None, max_length=128)


class WriteLedgerCreateRequest(BaseModel):
    ledger_id: str | None = Field(default=None, min_length=3, max_length=128)
    ledger_name: str = Field(min_length=1, max_length=255)
    currency: str = Field(default="CNY", min_length=1, max_length=16)


class WriteLedgerMetaUpdateRequest(WriteBaseRequest):
    ledger_name: str | None = Field(default=None, min_length=1, max_length=255)
    currency: str | None = Field(default=None, min_length=1, max_length=16)


class WriteTransactionCreateRequest(WriteBaseRequest):
    tx_type: Literal["expense", "income", "transfer"] = "expense"
    amount: float
    happened_at: datetime
    note: str | None = None
    category_name: str | None = None
    category_kind: Literal["expense", "income", "transfer"] | None = None
    account_name: str | None = None
    from_account_name: str | None = None
    to_account_name: str | None = None
    category_id: str | None = None
    account_id: str | None = None
    from_account_id: str | None = None
    to_account_id: str | None = None
    tags: str | list[str] | None = None
    tag_ids: list[str] | None = None
    attachments: list[dict[str, Any]] | None = None


class WriteTransactionUpdateRequest(WriteBaseRequest):
    tx_type: Literal["expense", "income", "transfer"] | None = None
    amount: float | None = None
    happened_at: datetime | None = None
    note: str | None = None
    category_name: str | None = None
    category_kind: Literal["expense", "income", "transfer"] | None = None
    account_name: str | None = None
    from_account_name: str | None = None
    to_account_name: str | None = None
    category_id: str | None = None
    account_id: str | None = None
    from_account_id: str | None = None
    to_account_id: str | None = None
    tags: str | list[str] | None = None
    tag_ids: list[str] | None = None
    attachments: list[dict[str, Any]] | None = None


class WriteEntityDeleteRequest(WriteBaseRequest):
    pass


class WriteAccountCreateRequest(WriteBaseRequest):
    name: str = Field(min_length=1, max_length=255)
    account_type: str | None = None
    currency: str | None = None
    initial_balance: float | None = None


class WriteAccountUpdateRequest(WriteBaseRequest):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    account_type: str | None = None
    currency: str | None = None
    initial_balance: float | None = None


class WriteCategoryCreateRequest(WriteBaseRequest):
    name: str = Field(min_length=1, max_length=255)
    kind: Literal["expense", "income", "transfer"]
    level: int | None = None
    sort_order: int | None = None
    icon: str | None = None
    icon_type: str | None = None
    custom_icon_path: str | None = None
    icon_cloud_file_id: str | None = None
    icon_cloud_sha256: str | None = None
    parent_name: str | None = None


class WriteCategoryUpdateRequest(WriteBaseRequest):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    kind: Literal["expense", "income", "transfer"] | None = None
    level: int | None = None
    sort_order: int | None = None
    icon: str | None = None
    icon_type: str | None = None
    custom_icon_path: str | None = None
    icon_cloud_file_id: str | None = None
    icon_cloud_sha256: str | None = None
    parent_name: str | None = None


class WriteTagCreateRequest(WriteBaseRequest):
    name: str = Field(min_length=1, max_length=255)
    color: str | None = None


class WriteTagUpdateRequest(WriteBaseRequest):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    color: str | None = None


class WorkspaceWriteBaseRequest(BaseModel):
    request_id: str | None = Field(default=None, max_length=128)


class WorkspaceAccountCreateRequest(WorkspaceWriteBaseRequest):
    name: str = Field(min_length=1, max_length=255)
    account_type: str | None = None
    currency: str | None = None
    initial_balance: float | None = None


class WorkspaceAccountUpdateRequest(WorkspaceWriteBaseRequest):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    account_type: str | None = None
    currency: str | None = None
    initial_balance: float | None = None


class WorkspaceCategoryCreateRequest(WorkspaceWriteBaseRequest):
    name: str = Field(min_length=1, max_length=255)
    kind: Literal["expense", "income", "transfer"]
    level: int | None = None
    sort_order: int | None = None
    icon: str | None = None
    icon_type: str | None = None
    custom_icon_path: str | None = None
    icon_cloud_file_id: str | None = None
    icon_cloud_sha256: str | None = None
    parent_name: str | None = None


class WorkspaceCategoryUpdateRequest(WorkspaceWriteBaseRequest):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    kind: Literal["expense", "income", "transfer"] | None = None
    level: int | None = None
    sort_order: int | None = None
    icon: str | None = None
    icon_type: str | None = None
    custom_icon_path: str | None = None
    icon_cloud_file_id: str | None = None
    icon_cloud_sha256: str | None = None
    parent_name: str | None = None


class WorkspaceTagCreateRequest(WorkspaceWriteBaseRequest):
    name: str = Field(min_length=1, max_length=255)
    color: str | None = None


class WorkspaceTagUpdateRequest(WorkspaceWriteBaseRequest):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    color: str | None = None


class AdminDeviceOut(BaseModel):
    id: str
    name: str
    platform: str
    app_version: str | None = None
    os_version: str | None = None
    device_model: str | None = None
    last_ip: str | None = None
    created_at: datetime
    last_seen_at: datetime
    is_online: bool
    user_id: str
    user_email: str


class AdminDeviceListOut(BaseModel):
    total: int
    items: list[AdminDeviceOut]


class AttachmentUploadOut(BaseModel):
    file_id: str
    ledger_id: str
    sha256: str
    size: int
    mime_type: str | None = None
    file_name: str | None = None
    created_at: datetime


class AttachmentExistsItem(BaseModel):
    sha256: str
    exists: bool
    file_id: str | None = None
    size: int | None = None
    mime_type: str | None = None


class AttachmentBatchExistsRequest(BaseModel):
    ledger_id: str
    sha256_list: list[str] = Field(default_factory=list)


class AttachmentBatchExistsResponse(BaseModel):
    items: list[AttachmentExistsItem] = Field(default_factory=list)
