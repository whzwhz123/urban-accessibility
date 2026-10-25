"""Fast startup diagnostics without changing packages or source datasets."""
import importlib.util
from pathlib import Path
import sys
import os

def main():
    missing=[name for name in ['numpy','pandas','scipy','geopandas','pyogrio','shapely','pyproj','openpyxl','rasterio'] if importlib.util.find_spec(name) is None]
    root=Path(__file__).resolve().parents[1]
    data=Path(os.environ.get('URBAN_DATA_ROOT',root/'data'))
    datasets=[data/'CPTOND-2025/city_list/AMap_adcode_citycode.csv',data/'CPTOND-2025/dataset/bus/shapefiles']
    if missing:print('缺少Python依赖：'+', '.join(missing)+'；请安装Back_end/requirements.txt',file=sys.stderr)
    for file in datasets:
        if not file.exists():print('未配置全国数据集，可在界面注册城市并上传自有数据：'+str(file))
    if missing:return 1
    import numpy
    if int(numpy.__version__.split('.')[0])>=2:print('当前项目使用NumPy<2，请使用受约束的依赖环境',file=sys.stderr);return 1
    print('本地依赖与城市目录检查通过');return 0
if __name__=='__main__':raise SystemExit(main())
