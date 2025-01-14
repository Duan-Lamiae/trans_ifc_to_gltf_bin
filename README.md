###### 安装conda环境

在conda环境中下载

下载对应操作系统的Anconda，然后将相应的配置设置在环境变量中

###### 创建环境：

```bash
conda create -n ifc_env python=3.9
```

###### 激活环境：

```bash
conda activate ifc_env
```

###### 在conda环境中下载pythonocc-core（读取几何图形的库）

```bash
conda install -c conda-forge pythonocc-core
```

建议下载ifcopenshell的zip解压缩手动放进环境中
包在当前目录下了：ifcopenshell-python-0.8.0-py39-win64.zip，解压缩以后命名为ifcopenshell
这个是window版的对应python3.9的
将整个 ifcopenshell 文件夹复制到您的 conda 环境的 site-packages 目录中
site-packages 目录通常在：C:\Users\您的用户名\.conda\envs\gltf\Lib\site-packages\

###### 使用pip install

```bash
pip install -r requirements.txt
```

###### 最后运行：

```bash
python ve_bin_gltf.py
```

