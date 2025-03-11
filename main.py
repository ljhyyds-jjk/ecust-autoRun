import aiohttp
import asyncio
from datetime import datetime, timedelta
import re
import json
import os
import logging
from logging.handlers import RotatingFileHandler

# 配置日志
def setup_logger():
    logger = logging.getLogger("ecust_run")
    logger.setLevel(logging.INFO)
    
    # 创建控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter('%(message)s')
    console_handler.setFormatter(console_format)
    
    # 创建文件处理器 - 使用RotatingFileHandler以便控制日志大小
    file_handler = RotatingFileHandler(
        "ecust_run.log", maxBytes=10*1024*1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.INFO)
    file_format = logging.Formatter('%(asctime)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    file_handler.setFormatter(file_format)
    
    # 添加处理器到logger
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
    return logger

# 创建日志实例
logger = setup_logger()

# 多组账号密码配置，增加延时设置
login_credentials = [
    {"iphone": "13810105050", "password": "gg112233", "delay": 0},  # 默认不延时
    {"iphone": "18040407070", "password": "tt667788", "delay": 3},  # 延时3s
    # 可以添加更多账号
]

# 创建缓存目录函数
def ensure_cache_dir():
    cache_dir = "ecust_cache"
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir)
    return cache_dir

# 保存凭证到缓存文件
def save_credentials_to_cache(phone, sessid, stuid):
    cache_dir = ensure_cache_dir()
    cache_file = os.path.join(cache_dir, f"{phone}_credentials.json")
    credentials = {
        "sessid": sessid,
        "stuid": stuid,
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    with open(cache_file, 'w') as f:
        json.dump(credentials, f)

# 从缓存文件读取凭证
def load_credentials_from_cache(phone):
    cache_dir = ensure_cache_dir()
    cache_file = os.path.join(cache_dir, f"{phone}_credentials.json")
    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            return json.load(f)
    return None

async def get_running_data(session, headers, log_prefix):
    """获取跑步数据统计"""
    url = 'https://run.ecust.edu.cn/api/RunningData/'
    async with session.get(url, headers=headers) as resp:
        response = await resp.json()
        if response.get('code') == 1:
            data = response.get('data', {})
            logger.info(f"{log_prefix}跑步数据统计: 目标有效={data.get('target_effective', 0)}, "
                  f"通用={data.get('universal', 0)}, 有效={data.get('effective', 0)}, "
                  f"早操={data.get('morning', 0)}")
        else:
            logger.info(f"{log_prefix}获取跑步数据统计失败: {response}")

async def run_test_for_account(credentials):
    phone = credentials["iphone"]
    password = credentials["password"]
    delay = credentials.get("delay", 0)  # 获取延时设置，默认为0
    log_prefix = f"[{phone}] "
    
    # 如果设置了延时，先等待指定的时间
    if delay > 0:
        logger.info(f"{log_prefix}设置了{delay}秒的延时，等待中...")
        await asyncio.sleep(delay)
    
    logger.info(f"{log_prefix}开始运行")
    
    # 基础请求头
    base_headers = {
        'accept': '*/*',
        'content-type': 'application/json',
        'lan': 'CH',
        'user-agent': 'chunTianChuangFu/1.3.1 (iPhone; iOS 18.2; Scale/3.00)',
        'accept-language': 'zh-Hans-CN;q=1, zh-Hant-CN;q=0.9, en-CN;q=0.8, ja-CN;q=0.7',
        'accept-encoding': 'gzip, deflate, br'
    }

    try:
        async with aiohttp.ClientSession() as session:
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
                # 步骤0：登录获取 sessid 和 stuid
                login_url = 'https://run.ecust.edu.cn/api/userLogin/'
                login_payload = {
                    "iphone": phone,
                    "password": password
                }
                
                logger.info(f"{log_prefix}步骤0：正在登录获取新凭证")
                
                async with session.post(login_url, json=login_payload, headers=base_headers) as resp:
                    # 从响应头中提取 sessionid
                    cookies = resp.headers.get('Set-Cookie', '')
                    sessid_match = re.search(r'sessionid=([^;]+)', cookies)
                    if not sessid_match:
                        logger.info(f"{log_prefix}登陆失败，停止运行。")
                        return False
                    sessid = sessid_match.group(1)
                    logger.info(f"{log_prefix}成功获取 sessionid: {sessid}")
                    
                    # 从响应体中提取 stuid (id)
                    login_response = await resp.json()
                    
                    if login_response.get('message') != "操作成功啦！":
                        logger.info(f"{log_prefix}登录失败，停止运行。")
                        return False
                        
                    stuid = str(login_response.get('data', {}).get('id'))
                    if not stuid:
                        logger.info(f"{log_prefix}未能从响应体获取学生ID，停止运行。")
                        return False
                        
                    logger.info(f"{log_prefix}成功获取学生ID: {stuid}")
                    
                    # 保存凭证到缓存
                    save_credentials_to_cache(phone, sessid, stuid)
                    logger.info(f"{log_prefix}凭证已保存到本地缓存")
            
            # 更新请求头，添加 sessionid cookie
            headers = base_headers.copy()
            headers['cookie'] = f'sessionid={sessid}'

            # 步骤1：请求 Runningverification 接口
            url1 = 'https://run.ecust.edu.cn/api/Runningverification/'
            logger.info(f"{log_prefix}步骤1：请求验证接口")
            async with session.get(url1, headers=headers) as resp:
                response1 = await resp.json()
                if response1.get('code') == -1:
                    logger.info(f"{log_prefix}验证通过，继续执行")
                else:
                    # 如果凭证无效或其他错误，尝试重新登录
                    if response1.get('code') == -2:
                        logger.info(f"{log_prefix}凭证无效，尝试重新登录")
                        # 清除缓存中的无效凭证
                        save_credentials_to_cache(phone, "", "")  # 清空凭证
                        # 重新运行此账号
                        return await run_test_for_account(credentials)
                    else:
                        logger.info(f"{log_prefix}账号重复跑步或其他错误，停止运行。响应: {response1}")
                        return False

            # 步骤2：请求 createLine 接口
            url2 = 'https://run.ecust.edu.cn/api/createLine/'
            payload2 = {
                "student_id": stuid,
                "pass_point": [
                    {
                        "point_name": "37",
                        "lng": "121.502959",
                        "lat": "30.82702",
                        "distance": 0.6
                    },
                    {
                        "point_name": "37",
                        "lng": "121.502959",
                        "lat": "30.82702",
                        "distance": 0.6
                    },
                    {
                        "point_name": "37",
                        "lng": "121.502959",
                        "lat": "30.82702",
                        "distance": 0.6
                    }
                ]
            }
            logger.info(f"{log_prefix}步骤2：创建路线")
            async with session.post(url2, json=payload2, headers=headers) as resp:
                response2 = await resp.json()
                record_id = response2.get('data', {}).get('record_id')
                if not record_id:
                    logger.info(f"{log_prefix}未获取到 record_id，停止运行。")
                    return False
                start_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                logger.info(f"{log_prefix}创建路线成功，record_id: {record_id}")
            
            # 步骤3：请求跑步数据统计（第一次）
            logger.info(f"{log_prefix}步骤3：获取跑步前数据统计")
            await get_running_data(session, headers, log_prefix)

            # 步骤4：等待10分钟，每60秒打印一次日志
            wait_time = 600  # 10分钟，单位为秒
            log_interval = 60  # 每60秒打印一次日志
            logger.info(f"{log_prefix}步骤4：开始等待10分钟")
            for remaining in range(wait_time, 0, -log_interval):
                logger.info(f"{log_prefix}剩余时间：{remaining} 秒")
                await asyncio.sleep(log_interval)
            logger.info(f"{log_prefix}等待结束")

            # 步骤5：请求 updateRecord 接口
            url3 = 'https://run.ecust.edu.cn/api/updateRecord/'
            end_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            payload3 = {
                "id": stuid,
                "pace": 301,
                "running_time": 601,
                "record_id": str(record_id),
                "mileage": 2001,
                "pass_point": 3,
                "start_time": start_time_str,
                "lat": 30.831605902777778,
                "step_count": 2000.4117647058823533,
                "end_time": end_time_str,
                "lng": 121.50631998697916,
                "student_id": stuid
            }
            logger.info(f"{log_prefix}步骤5：更新跑步记录")
            async with session.post(url3, json=payload3, headers=headers) as resp:
                response3 = await resp.json()
                if response3.get('code') != 1:
                    logger.info(f"{log_prefix}更新跑步记录失败。响应: {response3}")
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
    # 创建所有账号的运行任务
    tasks = [run_test_for_account(credentials) for credentials in login_credentials]
    
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
    logger.info(f"开始运行 {len(login_credentials)} 个账号")
    asyncio.run(main())
