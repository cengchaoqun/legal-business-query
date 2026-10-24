import asyncio
import json
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from .analytics import Analytics, AnalyticsError
from .config import Settings, ROOT
from .metadata import MetadataService
from .model_client import ModelClient, ModelError
from .query_service import QueryService
from .schemas import LoginRequest, QueryRequest, FeedbackRequest
from .storage import Storage, StorageConflict


def create_app(settings=None):
    cfg=settings or Settings()
    @asynccontextmanager
    async def lifespan(app):
        app.state.storage=Storage(cfg.app_database_url)
        app.state.storage.initialize()
        app.state.metadata=MetadataService(cfg.metadata_dir)
        app.state.model=ModelClient(cfg)
        app.state.analytics=Analytics(cfg.analytics_database_url,cfg.query_timeout_seconds,cfg.max_rows,cfg.max_result_bytes)
        app.state.query=QueryService(cfg,app.state.storage,app.state.metadata,app.state.model,app.state.analytics)
        yield
        app.state.storage.engine.dispose()
        app.state.analytics.engine.dispose()
    app=FastAPI(title='掌柜 · 法律经营问数',version='1.0.0',lifespan=lifespan)
    app.state.settings=cfg
    login_attempts={}

    @app.middleware('http')
    async def check_origin(request,call_next):
        if request.method not in ('GET','HEAD','OPTIONS'):
            origin=request.headers.get('origin')
            if origin and origin not in cfg.allowed_origins.split(','):
                return JSONResponse({'detail':'请求来源未授权'},status_code=403)
            if int(request.headers.get('content-length','0') or 0)>2_000_000:
                return JSONResponse({'detail':'请求内容过大'},status_code=413)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        return response

    def user(request:Request):
        current=app.state.storage.get_session(request.cookies.get('legal_session',''))
        if not current:
            raise HTTPException(401,'请先登录')
        return current

    def admin(current=Depends(user)):
        if current['role']!='admin':
            raise HTTPException(403,'此功能仅管理员可用')
        return current

    def owned_query(qid,current):
        row=app.state.storage.get_query(qid)
        if not row or (row['user_id']!=current['id'] and current['role']!='admin'):
            raise HTTPException(404,'查询记录不存在')
        return row

    def status_watermark():
        try:
            return app.state.analytics.get_watermark()
        except AnalyticsError:
            return None

    @app.exception_handler(StorageConflict)
    async def storage_conflict(request,error):
        return JSONResponse({'detail':str(error)},status_code=409)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request,error):
        return JSONResponse({'detail':'应用数据库暂不可用，请稍后重试'},status_code=503)

    @app.get('/api/health')
    def health():
        return {'status':'ok','model_mode':cfg.model_mode}

    @app.post('/api/auth/login')
    def login(body:LoginRequest,response:Response,request:Request):
        key=(request.client.host if request.client else '',body.username)
        now=time.monotonic()
        if len(login_attempts)>1000:
            login_attempts.clear()
        attempts=[x for x in login_attempts.get(key,[]) if now-x<60]
        if len(attempts)>=10:
            raise HTTPException(429,'登录尝试过多，请稍后再试')
        current=app.state.storage.authenticate(body.username,body.password)
        if not current:
            login_attempts[key]=attempts+[now]
            raise HTTPException(401,'用户名或密码错误')
        login_attempts.pop(key,None)
        token=app.state.storage.create_session(current['id'])
        response.set_cookie('legal_session',token,httponly=True,secure=cfg.cookie_secure,samesite='strict',max_age=28800,path='/')
        return current

    @app.post('/api/auth/logout')
    def logout(request:Request,response:Response,current=Depends(user)):
        app.state.storage.delete_session(request.cookies.get('legal_session',''))
        response.delete_cookie('legal_session',path='/')
        return {'ok':True}

    @app.get('/api/me')
    def me(current=Depends(user)):
        return current

    @app.post('/api/conversations')
    def create_conversation(current=Depends(user)):
        row=app.state.storage.create_conversation(current['id'])
        return {'conversation_id':row['id']}

    @app.get('/api/conversations')
    def conversations(current=Depends(user)):
        return {'items':app.state.storage.list_conversations(current['id'])}

    @app.get('/api/conversations/{cid}/queries')
    def conversation(cid:str,current=Depends(user)):
        conv=app.state.storage.get_conversation(cid,current['id'])
        if conv is None:
            raise HTTPException(404,'会话不存在')
        return {'conversation':conv,'items':app.state.storage.list_queries(cid)}

    @app.post('/api/conversations/{cid}/queries')
    async def query(cid:str,body:QueryRequest,response:Response,current=Depends(user)):
        try:
            result=await app.state.query.run(cid,current,body)
        except LookupError:
            raise HTTPException(404,'会话不存在')
        if result['status']=='processing':
            response.status_code=202
        elif result['status']=='failed':
            response.status_code=504 if result.get('error_code') in ('MODEL_TIMEOUT','QUERY_TIMEOUT') else 503
        return result

    @app.get('/api/queries/{qid}')
    def get_query(qid:str,current=Depends(user)):
        return owned_query(qid,current)

    @app.post('/api/queries/{qid}/feedback')
    def feedback(qid:str,body:FeedbackRequest,current=Depends(user)):
        row=owned_query(qid,current)
        if row['user_id']!=current['id']:
            raise HTTPException(403,'仅提交人可以反馈自己的查询')
        return app.state.storage.add_feedback(qid,current['id'],body.feedback_type,body.comment)

    @app.get('/api/metadata')
    def metadata(current=Depends(user)):
        package=app.state.metadata.snapshot()
        return {**package,'version':package['schema']['version'],'synthetic':package['schema']['synthetic'],'data_watermark':status_watermark(),'model_mode':cfg.model_mode}

    @app.post('/api/admin/metadata/import')
    async def import_metadata(request:Request,current=Depends(admin)):
        raw=await request.body()
        if len(raw)>2_000_000:
            raise HTTPException(413,'配置包过大')
        try:
            version=app.state.metadata.publish(json.loads(raw),current['username'])
        except (ValueError,TypeError,KeyError,AttributeError) as error:
            raise HTTPException(422,str(error))
        return {'version':version,'message':'配置已校验并发布'}

    @app.get('/api/admin/query-runs')
    def runs(current=Depends(admin)):
        return {'items':app.state.storage.all_query_runs()}

    @app.get('/api/admin/system/status')
    def system(current=Depends(admin)):
        return {'model':{'mode':cfg.model_mode,'name':cfg.model_name,'base_url':cfg.model_base_url,'configured':bool(cfg.model_base_url) or cfg.model_mode=='mock'},'database':{'dialect':app.state.analytics.engine.dialect.name,'read_only':True},'limits':{'query_timeout_seconds':cfg.query_timeout_seconds,'max_rows':cfg.max_rows},'metadata_version':app.state.metadata.version,'data_watermark':status_watermark(),'synthetic':True}

    @app.post('/api/admin/model/test')
    async def model_test(current=Depends(admin)):
        try:
            return await app.state.model.test()
        except ModelError as error:
            return JSONResponse({'ok':False,'mode':cfg.model_mode,'message':str(error)},status_code=503)

    return app


app=create_app()
