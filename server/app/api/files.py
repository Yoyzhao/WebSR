"""文件路由（api-contract §4.2）：上传 + 内容获取。

上传图片整体读入内存（上限 APP_MAX_UPLOAD_MB，默认 50MB）——图片需要完整
字节做 Pillow 二次解码校验，且上限远小于模型文件，内存代价可控；
模型导入（T-604）才是流式场景。
"""
from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import FileResponse

from ..services import media_store

router = APIRouter(prefix="/api/files", tags=["files"])


@router.post("/upload", status_code=201)
async def upload_file(file: UploadFile = File(...)) -> dict:
    data = await file.read()
    return media_store.save_upload(data, file.filename or "upload")


@router.get("/{file_id}/content")
def file_content(file_id: str, variant: str = Query(default="original")) -> FileResponse:
    path, media_type = media_store.get_content(file_id, variant)
    return FileResponse(path, media_type=media_type)
