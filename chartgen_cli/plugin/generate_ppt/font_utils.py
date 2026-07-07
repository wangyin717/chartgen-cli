#!/usr/bin/env python3
"""
字体工具模块 - 解决跨平台字体渲染一致性问题
"""

import platform
import subprocess
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# 不同系统下的推荐中文字体
FONT_FALLBACK_MAP = {
    'Darwin': [  # macOS
        'PingFang SC',
        'Hiragino Sans GB', 
        'Noto Sans SC',
        'Microsoft YaHei',
        'SimHei'
    ],
    'Linux': [   # Linux
        'Noto Sans CJK SC',
        'Noto Sans SC',
        'WenQuanYi Micro Hei',
        'AR PL UMing CN',
        'DejaVu Sans'
    ],
    'Windows': [ # Windows
        'Microsoft YaHei',
        'SimHei',
        'Noto Sans SC',
        '微软雅黑'
    ]
}

def get_system_fonts() -> List[str]:
    """
    获取系统已安装的字体列表
    
    Returns:
        List[str]: 字体名称列表
    """
    system = platform.system()
    fonts = []
    
    try:
        if system == 'Darwin':  # macOS
            try:
                result = subprocess.run(['fc-list', ':lang=zh'], 
                                      capture_output=True, text=True, timeout=10)
            except FileNotFoundError:
                # macOS可能没有安装fontconfig，使用系统_profiler
                try:
                    result = subprocess.run(['system_profiler', 'SPFontsDataType'], 
                                          capture_output=True, text=True, timeout=10)
                    if result.returncode == 0:
                        # 简单解析字体信息
                        for line in result.stdout.split('\n'):
                            if 'PostScript Name:' in line:
                                font_name = line.split('PostScript Name:')[1].strip()
                                if font_name and ('Chinese' in font_name or 'SC' in font_name or 'CN' in font_name):
                                    fonts.append(font_name)
                        return list(set(fonts))
                except Exception:
                    pass
                # 如果都失败，返回预定义的常用字体
                return ['PingFang SC', 'Hiragino Sans GB', 'Noto Sans SC', 'Microsoft YaHei']
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if ':' in line:
                        font_name = line.split(':')[1].strip().split(',')[0]
                        if font_name:
                            fonts.append(font_name)
                            
        elif system == 'Linux':
            try:
                result = subprocess.run(['fc-list', ':lang=zh'], 
                                      capture_output=True, text=True, timeout=10)
            except FileNotFoundError:
                logger.warning("fc-list命令不可用，返回预定义字体列表")
                return ['Noto Sans CJK SC', 'Noto Sans SC', 'WenQuanYi Micro Hei']
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if ':' in line:
                        font_parts = line.split(':')
                        if len(font_parts) > 1:
                            font_name = font_parts[1].strip().split(',')[0]
                            if font_name:
                                fonts.append(font_name)
                                
        elif system == 'Windows':
            # Windows下获取字体较为复杂，这里提供简化版本
            import winreg
            try:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 
                                   r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, i)
                        if 'Chinese' in name or 'Sim' in name or 'YaHei' in name:
                            fonts.append(name.replace(' (TrueType)', ''))
                        i += 1
                    except OSError:
                        break
                winreg.CloseKey(key)
            except Exception as e:
                logger.warning(f"无法读取Windows注册表字体信息: {e}")
                
    except Exception as e:
        logger.error(f"获取系统字体时出错: {e}")
    
    return list(set(fonts))  # 去重

def check_font_availability(font_name: str) -> bool:
    """
    检查指定字体是否可用
    
    Args:
        font_name: 字体名称
        
    Returns:
        bool: 字体是否可用
    """
    system_fonts = get_system_fonts()
    return font_name in system_fonts

def get_available_chinese_fonts(preferred_fonts: Optional[List[str]] = None) -> List[str]:
    """
    获取系统中可用的中文字体，按优先级排序
    
    Args:
        preferred_fonts: 优先检查的字体列表
        
    Returns:
        List[str]: 可用的字体列表
    """
    system = platform.system()
    
    # 如果没有指定优先字体，则使用系统默认推荐
    if preferred_fonts is None:
        preferred_fonts = FONT_FALLBACK_MAP.get(system, FONT_FALLBACK_MAP['Linux'])
    
    system_fonts = get_system_fonts()
    available_fonts = []
    
    # 按优先级检查字体可用性
    for font in preferred_fonts:
        if font in system_fonts:
            available_fonts.append(font)
    
    # 如果没有找到推荐字体，返回所有可用的中文字体
    if not available_fonts:
        available_fonts = system_fonts
    
    logger.info(f"系统: {system}, 可用中文字体: {available_fonts[:5]}...")  # 只显示前5个
    return available_fonts

def generate_font_stack(available_fonts: List[str], include_web_fonts: bool = True) -> str:
    """
    生成CSS字体栈
    
    Args:
        available_fonts: 可用字体列表
        include_web_fonts: 是否包含网络字体
        
    Returns:
        str: CSS字体栈字符串
    """
    font_list = available_fonts.copy()
    
    # 添加通用字体族作为后备
    generic_fonts = ['sans-serif']
    
    # 如果启用网络字体且Noto Sans SC可用，将其放在前面
    if include_web_fonts and 'Noto Sans SC' not in font_list:
        font_list.insert(0, 'Noto Sans SC')
    
    # 构造字体栈
    font_stack = ', '.join([f"'{font}'" for font in font_list] + generic_fonts)
    return font_stack

def get_optimal_font_settings() -> Dict[str, any]:
    """
    获取最优的字体设置配置
    
    Returns:
        Dict: 包含字体栈和其他CSS设置的配置字典
    """
    available_fonts = get_available_chinese_fonts()
    font_stack = generate_font_stack(available_fonts)
    
    config = {
        'font_family': font_stack,
        'css_properties': {
            'font-family': font_stack,
            '-webkit-font-smoothing': 'antialiased',
            '-moz-osx-font-smoothing': 'grayscale',
            'text-rendering': 'optimizeLegibility',
            '-webkit-text-size-adjust': '100%',
            '-ms-text-size-adjust': '100%'
        },
        'available_fonts': available_fonts,
        'system': platform.system(),
        'has_noto_sans_sc': 'Noto Sans SC' in available_fonts or 'Noto Sans CJK SC' in available_fonts
    }
    
    return config

def print_font_diagnostics():
    """
    打印字体诊断信息（用于调试）
    """
    print("=== 字体诊断信息 ===")
    print(f"操作系统: {platform.system()} {platform.release()}")
    print(f"架构: {platform.machine()}")
    
    available_fonts = get_available_chinese_fonts()
    print(f"\n可用中文字体 ({len(available_fonts)} 个):")
    for i, font in enumerate(available_fonts[:10]):  # 只显示前10个
        print(f"  {i+1}. {font}")
    if len(available_fonts) > 10:
        print(f"  ... 还有 {len(available_fonts) - 10} 个字体")
    
    config = get_optimal_font_settings()
    print(f"\n推荐字体栈:")
    print(f"  {config['font_family']}")
    print(f"包含 Noto Sans SC: {config['has_noto_sans_sc']}")

if __name__ == "__main__":
    # 运行诊断
    print_font_diagnostics()