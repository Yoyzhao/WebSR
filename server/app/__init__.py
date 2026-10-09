"""WebSR 后端应用包（FastAPI）。

分层约定（tech-arch §2.2，依赖单向禁止循环）：
  api/      路由层（T-608）
  core/     配置、日志、错误处理（T-601）
  engine/   M2 推理引擎 + M4 图像处理（T-802~T-805）
  models/   SQLAlchemy 实体（T-602）
  schemas/  Pydantic DTO（T-604 起）
  tasks/    M1 任务中心：队列 / 状态机 / 进度广播（T-606 / T-607）
"""
