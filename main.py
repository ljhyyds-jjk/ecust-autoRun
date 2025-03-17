import aiohttp
import asyncio
from datetime import datetime
import json
import os
import logging
from logging.handlers import RotatingFileHandler
import random
import time
from contextlib import asynccontextmanager
import yarl  # 添加 yarl 导入

# 全局常量配置
ACCOUNT_CONFIG_FILE = "ecust_accounts.json"
CACHE_DIR = "ecust_cache"
API_BASE_URL = "https://run.ecust.edu.cn/api"
LOG_FILE = "ecust_run.log"
DEFAULT_RETRY_COUNT = 3
DEFAULT_RETRY_DELAY = 2  # 重试等待时间（秒）

# 基础请求头
DEFAULT_HEADERS = {
    'accept': '*/*',
    'content-type': 'application/json',
    'lan': 'CH',
    'user-agent': 'chunTianChuangFu/1.3.1 (iPhone; iOS 18.4; Scale/3.00)',
    'accept-language': 'zh-Hans-CN;q=1, zh-Hant-CN;q=0.9',
    'accept-encoding': 'gzip, deflate, br'
}

# API端点
API_ENDPOINTS = {
    "login": f"{API_BASE_URL}/userLogin/",
    "verify": f"{API_BASE_URL}/Runningverification/",
    "create_line": f"{API_BASE_URL}/createLine/",
    "update_record": f"{API_BASE_URL}/updateRecord/",
    "running_data": f"{API_BASE_URL}/RunningData/"
}

# 配置日志
def setup_logger():
    logger = logging.getLogger("ecust_run")
    logger.setLevel(logging.INFO)
    
    # 清理之前的处理器，避免重复日志
    if logger.handlers:
        logger.handlers.clear()
    
    # 创建控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter('%(message)s')
    console_handler.setFormatter(console_format)
    
    # 创建文件处理器 - 使用RotatingFileHandler以便控制日志大小
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.INFO)
    file_format = logging.Formatter('%(asctime)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    file_handler.setFormatter(file_format)
    
    # 添加过滤器，排除等待进程日志
    class WaitingFilter(logging.Filter):
        def filter(self, record):
            return not hasattr(record, 'waiting_log') or not record.waiting_log
    
    file_handler.addFilter(WaitingFilter())
    
    # 添加处理器到logger
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
    return logger

# 创建日志实例
logger = setup_logger()

# 为等待过程创建特殊的日志函数
def log_waiting(message):
    """仅输出到控制台，不记录到日志文件"""
    record = logging.LogRecord(
        name=logger.name,
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=message,
        args=(),
        exc_info=None
    )
    record.waiting_log = True  # 标记为等待日志
    logger.handle(record)

# 加载账号配置
def load_account_config():
    try:
        with open(ACCOUNT_CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
            
            # 验证配置格式是否正确
            if not isinstance(config, list):
                logger.error(f"账号配置文件格式错误，应为列表")
                return None
                
            for account in config:
                if not all(key in account for key in ["iphone", "password"]):
                    logger.error(f"账号配置缺少必要信息")
                    return None
                
            return config
    except FileNotFoundError:
        logger.error(f"账号配置文件 {ACCOUNT_CONFIG_FILE} 不存在")
        return None
    except json.JSONDecodeError:
        logger.error(f"账号配置文件解析失败，请检查JSON格式")
        return None
    except Exception as e:
        logger.error(f"加载账号配置时出错: {e}")
        return None

# 创建默认账号配置文件
def create_default_account_config():
    default_config = [
        {"iphone": "13800000000", "password": "yourpassword", "delay": "0-50"}
    ]
    
    try:
        with open(ACCOUNT_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, indent=2, ensure_ascii=False)
        logger.info(f"已创建默认账号配置文件 {ACCOUNT_CONFIG_FILE}，请编辑后重新运行")
        return True
    except Exception as e:
        logger.error(f"创建账号配置文件失败: {e}")
        return False

# 创建缓存目录函数
def ensure_cache_dir():
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)
    return CACHE_DIR

# 保存凭证到缓存文件
def save_credentials_to_cache(phone, sessid, stuid):
    cache_dir = ensure_cache_dir()
    cache_file = os.path.join(cache_dir, f"{phone}_credentials.json")
    credentials = {
        "sessid": sessid,
        "stuid": stuid,
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump(credentials, f, ensure_ascii=False)

# 从缓存文件读取凭证
def load_credentials_from_cache(phone):
    cache_dir = ensure_cache_dir()
    cache_file = os.path.join(cache_dir, f"{phone}_credentials.json")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"[{phone}] 读取缓存凭证失败: {e}")
    return None

# 生成随机延时
def generate_delay(delay_setting):
    """根据配置生成随机延时"""
    if isinstance(delay_setting, str) and "-" in delay_setting:
        try:
            min_delay, max_delay = map(int, delay_setting.split("-"))
            return random.randint(min_delay, max_delay)
        except (ValueError, TypeError):
            return 0
    else:
        try:
            return int(delay_setting)
        except (ValueError, TypeError):
            return 0

# 创建HTTP客户端上下文管理器
@asynccontextmanager
async def get_http_client():
    """创建一个共享的HTTP客户端会话上下文管理器"""
    timeout = aiohttp.ClientTimeout(total=30)  # 设置30秒超时
    async with aiohttp.ClientSession(timeout=timeout) as session:
        yield session

# 通用API请求函数，包含重试逻辑
async def api_request(session, method, url, headers=None, json_data=None, max_retries=DEFAULT_RETRY_COUNT, log_prefix=""):
    """
    发送API请求，自动进行重试
    :param session: aiohttp会话
    :param method: 请求方法 ('GET' 或 'POST')
    :param url: 请求URL
    :param headers: 请求头
    :param json_data: POST请求的JSON数据
    :param max_retries: 最大重试次数
    :param log_prefix: 日志前缀
    :return: JSON响应或None(失败时)
    """
    if headers is None:
        headers = DEFAULT_HEADERS.copy()
        
    retries = 0
    while retries <= max_retries:
        try:
            if method.upper() == 'GET':
                async with session.get(url, headers=headers) as response:
                    return await response.json()
            elif method.upper() == 'POST':
                async with session.post(url, headers=headers, json=json_data) as response:
                    return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            retries += 1
            if retries > max_retries:
                logger.error(f"{log_prefix}请求失败，已达最大重试次数: {e}")
                return None
            
            wait_time = DEFAULT_RETRY_DELAY * retries
            logger.info(f"{log_prefix}请求失败，{wait_time}秒后第{retries}次重试")
            await asyncio.sleep(wait_time)
        except Exception as e:
            logger.error(f"{log_prefix}请求异常: {e}")
            return None
    
    return None

# 获取跑步数据统计
async def get_running_data(session, headers, log_prefix):
    """获取跑步数据统计"""
    response = await api_request(session, 'GET', API_ENDPOINTS["running_data"], headers, log_prefix=log_prefix)
    if response and response.get('code') == 1:
        data = response.get('data', {})
        logger.info(f"{log_prefix}跑步数据统计: 目标有效={data.get('target_effective', 0)}, "
              f"通用={data.get('universal', 0)}, 有效={data.get('effective', 0)}, "
              f"早操={data.get('morning', 0)}")
    else:
        logger.info(f"{log_prefix}获取跑步数据统计失败: {response}")

# 生成随机跑步数据
def generate_random_run_data(wait_multiplier):
    """生成随机的跑步相关数据"""
    data = {
        # 随机化经纬度（±0.1范围内浮动）
        "lat": 30.831605902777778 + random.uniform(-0.1, 0.1),
        "lng": 121.50631998697916 + random.uniform(-0.1, 0.1),
        
        # 随机化其他数据（按等待时间的比例调整）
        "running_time": int(601 * wait_multiplier),
        "mileage": int(2001 * wait_multiplier),
        "step_count": 2000.4117647058823533 * wait_multiplier,
        "pace": 301
    }
    return data

async def login_account(session, phone, password, log_prefix):
    """登录账号并获取凭证"""
    login_payload = {"iphone": phone, "password": password}
    
    logger.info(f"{log_prefix}正在登录获取新凭证")
    
    response = await api_request(
        session, 'POST', API_ENDPOINTS["login"], 
        json_data=login_payload, 
        log_prefix=log_prefix
    )
    
    if not response or response.get('message') != "操作成功啦！":
        logger.info(f"{log_prefix}登录失败，响应: {response}")
        return None, None
    
    # 从响应头中提取sessionid
    # 修复：使用 yarl.URL 而不是字符串
    login_url = yarl.URL(API_ENDPOINTS["login"])
    cookies = session.cookie_jar.filter_cookies(login_url)
    sessid = None
    
    # 修复：正确处理不同类型的cookie对象
    for name, cookie in cookies.items():
        if name == 'sessionid':
            sessid = cookie.value
            break
    
    if not sessid:
        logger.info(f"{log_prefix}未找到sessionid，登录失败")
        return None, None
        
    # 从响应体中提取学生ID
    stuid = str(response.get('data', {}).get('id'))
    if not stuid:
        logger.info(f"{log_prefix}未能从响应体获取学生ID，登录失败")
        return None, None
        
    logger.info(f"{log_prefix}登录成功，获取凭证: sessid={sessid}, stuid={stuid}")
    return sessid, stuid

async def run_test_for_account(credentials, skip_delay=False):
    phone = credentials["iphone"]
    password = credentials["password"]
    
    # 解析delay设置，支持范围格式（例如"0-50"）
    delay_setting = credentials.get("delay", "0")
    delay = generate_delay(delay_setting) if not skip_delay else 0
    
    log_prefix = f"[{phone}] "
    
    # 如果设置了延时且不是重新登录状态，先等待指定的时间
    if delay > 0 and not skip_delay:
        logger.info(f"{log_prefix}随机延时{delay}秒，等待中...")
        await asyncio.sleep(delay)
    elif skip_delay:
        logger.info(f"{log_prefix}重新登录模式，跳过随机延时")
    
    logger.info(f"{log_prefix}开始运行")
    
    try:
        async with get_http_client() as session:
            # 尝试从缓存加载凭证
            cached_credentials = load_credentials_from_cache(phone)
            sessid = None
            stuid = None
            
            if cached_credentials:
                sessid = cached_credentials.get("sessid")
                stuid = cached_credentials.get("stuid")
                timestamp = cached_credentials.get("timestamp")
                logger.info(f"{log_prefix}从缓存加载凭证成功 - sessid: {sessid}, stuid: {stuid}, 缓存时间: {timestamp}")
            
            # 如果没有缓存凭证，则进行登录
            if not sessid or not stuid:
                sessid, stuid = await login_account(session, phone, password, log_prefix)
                if not sessid or not stuid:
                    return False
                save_credentials_to_cache(phone, sessid, stuid)
                logger.info(f"{log_prefix}凭证已保存到本地缓存")
            
            # 更新请求头，添加 sessionid cookie
            headers = DEFAULT_HEADERS.copy()
            headers['cookie'] = f'sessionid={sessid}'

            # 步骤1：请求 Runningverification 接口
            logger.info(f"{log_prefix}步骤1：请求验证接口")
            response1 = await api_request(session, 'GET', API_ENDPOINTS["verify"], headers, log_prefix=log_prefix)
            if response1 and response1.get('code') == -1:
                logger.info(f"{log_prefix}验证通过，继续执行")
            else:
                # 如果凭证无效或其他错误，尝试重新登录
                if response1 and response1.get('code') == -2:
                    logger.info(f"{log_prefix}缓存凭证无效，尝试重新登录")
                    # 清除缓存中的无效凭证
                    save_credentials_to_cache(phone, "", "")  # 清空凭证
                    # 尝试重新登录并立即跑步，跳过延迟
                    sessid, stuid = await login_account(session, phone, password, log_prefix)
                    if not sessid or not stuid:
                        return False
                    save_credentials_to_cache(phone, sessid, stuid)
                    logger.info(f"{log_prefix}重新登录成功，继续跑步测试")
                    # 更新请求头，添加新的sessionid cookie
                    headers['cookie'] = f'sessionid={sessid}'
                    # 再次请求验证接口
                    response1 = await api_request(session, 'GET', API_ENDPOINTS["verify"], headers, log_prefix=log_prefix)
                    if response1 and response1.get('code') == -1:
                        logger.info(f"{log_prefix}验证通过，继续执行")
                    else:
                        logger.info(f"{log_prefix}即使重新登录后仍验证失败，停止运行。响应: {response1}")
                        return False
                else:
                    logger.info(f"{log_prefix}账号重复跑步或其他错误，停止运行。响应: {response1}")
                    return False

            # 步骤2：请求 createLine 接口（随机化distance）
            # 随机生成一个0.2-2.0之间保留一位小数的数值
            random_distance = round(random.uniform(0.2, 2.0), 1)
            payload2 = {
                "student_id": stuid,
                "pass_point": [
                    {
                        "point_name": "37",
                        "lng": "121.502959",
                        "lat": "30.82702",
                        "distance": random_distance
                    },
                    {
                        "point_name": "37",
                        "lng": "121.502959",
                        "lat": "30.82702",
                        "distance": random_distance
                    },
                    {
                        "point_name": "37",
                        "lng": "121.502959",
                        "lat": "30.82702",
                        "distance": random_distance
                    }
                ]
            }
            logger.info(f"{log_prefix}步骤2：创建路线 (distance={random_distance})")
            response2 = await api_request(session, 'POST', API_ENDPOINTS["create_line"], headers, payload2, log_prefix=log_prefix)
            if not response2 or not response2.get('data', {}).get('record_id'):
                logger.info(f"{log_prefix}未获取到 record_id，停止运行。")
                return False
            record_id = response2.get('data', {}).get('record_id')
            start_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            logger.info(f"{log_prefix}创建路线成功，record_id: {record_id}")
            
            # 步骤3：请求跑步数据统计（第一次）
            logger.info(f"{log_prefix}步骤3：获取跑步前数据统计")
            await get_running_data(session, headers, log_prefix)

            # 步骤4：随机化等待时间（600秒乘以1-1.3的随机数）
            wait_multiplier = random.uniform(1.0, 1.3)
            wait_time = int(600 * wait_multiplier)  # 随机化等待时间
            logger.info(f"{log_prefix}步骤4：开始等待{wait_time}秒")

            # 实现每秒更新的倒计时
            start_time = time.time()
            end_time = start_time + wait_time
            while time.time() < end_time:
                remaining = int(end_time - time.time())
                log_waiting(f"{log_prefix}倒计时：{remaining}秒")
                await asyncio.sleep(1)
                # 清除上一行输出
                print("\r", end="")
            
            # 清除倒计时显示，打印完成信息
            print("\r", end="")
            logger.info(f"{log_prefix}等待结束，实际等待了{wait_time}秒")

            # 步骤5：请求 updateRecord 接口（随机化数据）
            end_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            random_run_data = generate_random_run_data(wait_multiplier)
            payload3 = {
                "id": stuid,
                "pace": random_run_data["pace"],
                "running_time": random_run_data["running_time"],
                "record_id": str(record_id),
                "mileage": random_run_data["mileage"],
                "pass_point": 3,
                "start_time": start_time_str,
                "lat": random_run_data["lat"],
                "step_count": random_run_data["step_count"],
                "end_time": end_time_str,
                "lng": random_run_data["lng"],
                "student_id": stuid
            }
            logger.info(f"{log_prefix}步骤5：更新跑步记录")
            response3 = await api_request(session, 'POST', API_ENDPOINTS["update_record"], headers, payload3, log_prefix=log_prefix)
            if not response3 or response3.get('code') != 1:
                logger.info(f"{log_prefix}更新跑步记录失败。账号可能被踢下线。响应: {response3}")
                return False
            logger.info(f"{log_prefix}更新跑步记录成功")
            
            # 步骤6：请求跑步数据统计（第二次）
            logger.info(f"{log_prefix}步骤6：获取跑步后数据统计")
            await get_running_data(session, headers, log_prefix)
            
            logger.info(f"{log_prefix}所有步骤成功完成。")
            return True
    except Exception as e:
        logger.error(f"{log_prefix}运行过程中出现异常: {e}", exc_info=True)
        return False

async def main():
    # 加载账号配置
    login_credentials = load_account_config()
    
    # 如果配置加载失败，创建默认配置文件并退出
    if login_credentials is None:
        create_default_account_config()
        logger.info("请编辑账号配置文件后重新运行")
        return
    
    # 如果没有账号配置，退出
    if len(login_credentials) == 0:
        logger.info("账号配置为空，请添加账号后重新运行")
        return
        
    # 创建所有账号的运行任务
    tasks = [run_test_for_account(credentials, skip_delay=False) for credentials in login_credentials]
    
    # 等待所有任务完成
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 统计结果
    success_count = 0
    fail_count = 0
    error_count = 0
    
    for result in results:
        if isinstance(result, Exception):
            error_count += 1
        elif result is True:
            success_count += 1
        else:
            fail_count += 1
    
    logger.info(f"\n运行完成统计:")
    logger.info(f"账号总数: {len(login_credentials)}")
    logger.info(f"成功数: {success_count}")
    logger.info(f"失败数: {fail_count}")
    if error_count > 0:
        logger.info(f"异常数: {error_count}")

if __name__ == "__main__":
    logger.info(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"开始加载账号配置...")
    asyncio.run(main())