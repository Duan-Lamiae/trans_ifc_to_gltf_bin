import ifcopenshell
from ifcopenshell import geom
import numpy as np
from pygltflib import GLTF2, BufferFormat, BufferView, Accessor, Mesh, Primitive, Node, Scene, Buffer, Material, Asset
import os
import tempfile
import base64
import json

def create_buffer_from_vertex_data(vertices, indices):
    """
    创建顶点和索引的buffer数据
    """
    # 将顶点数据转换为字节
    vertex_data = vertices.astype(np.float32).tobytes()
    index_data = indices.astype(np.uint32).tobytes()
    
    # 合并数据
    buffer_data = vertex_data + index_data
    
    # Base64编码
    buffer_uri = "data:application/octet-stream;base64," + base64.b64encode(buffer_data).decode('ascii')
    
    return buffer_uri, len(vertex_data), len(index_data)

def convert_numpy_types(obj):
    """转换 NumPy 类型为 Python 原生类型"""
    if isinstance(obj, (np.integer, np.floating)):
        return float(obj) if isinstance(obj, np.floating) else int(obj)
    elif isinstance(obj, np.ndarray):
        return [convert_numpy_types(x) for x in obj.tolist()]
    elif isinstance(obj, (list, tuple)):
        return [convert_numpy_types(x) for x in obj]
    elif isinstance(obj, dict):
        return {k: convert_numpy_types(v) for k, v in obj.items()}
    return obj

def convert_all_numpy_in_gltf(gltf):
    """确保GLTF对象中的所有NumPy类型都被转换"""
    if hasattr(gltf, 'nodes'):
        for node in gltf.nodes:
            if hasattr(node, 'matrix'):
                node.matrix = convert_numpy_types(node.matrix)
            if hasattr(node, 'extras'):
                node.extras = convert_numpy_types(node.extras)
    
    if hasattr(gltf, 'accessors'):
        for accessor in gltf.accessors:
            if hasattr(accessor, 'max'):
                accessor.max = convert_numpy_types(accessor.max)
            if hasattr(accessor, 'min'):
                accessor.min = convert_numpy_types(accessor.min)
    
    if hasattr(gltf, 'extras'):
        gltf.extras = convert_numpy_types(gltf.extras)
    
    return gltf

def ifc_to_gltf(ifc_file_path, gltf_file_path):
    """
    将IFC文件转换为GLTF格式，保持每个构件的唯一性，并添加扩展支持
    """
    # 创建 .bin 文件路径
    bin_file_path = os.path.splitext(gltf_file_path)[0] + '.bin'
    
    # 检查文件是否存在
    if not os.path.exists(ifc_file_path):
        raise FileNotFoundError(f"IFC文件不存在: {ifc_file_path}")
    
    # 检查文件大小
    if os.path.getsize(ifc_file_path) == 0:
        raise ValueError(f"IFC文件为空: {ifc_file_path}")
        
    try:
        # 加载IFC文件
        print(f"正在加载IFC文件: {ifc_file_path}")
        ifc_file = ifcopenshell.open(ifc_file_path)
        print("IFC文件加载成功")
    except Exception as e:
        print(f"加载IFC文件时出错: {str(e)}")
        print("请确保IFC文件格式正确且未损坏")
        return
    
    # 创建GLTF对象并添加扩展支持
    gltf = GLTF2()
    gltf.asset = Asset(version="2.0", generator="IfcOpenShell GLTF Exporter")
    gltf.extensionsUsed = [
        "KHR_materials_specular",
        "KHR_materials_volume",
        "FB_ngon_encoding"
    ]
    
    # 设置IFC几何引擎的参数
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    
    # 用于存储所有buffer数据
    all_buffer_data = bytearray()
    current_buffer_length = 0
    
    # 创建材质字典，用于重用相同材质
    material_dict = {}
    
    def create_material_with_extensions(product):
        """创建带扩展的材质"""
        material_name = "DefaultMaterial"
        if hasattr(product, "Material") and product.Material:
            material_name = product.Material.Name
        
        # 检查是否已经创建过该材质
        if material_name in material_dict:
            return material_dict[material_name]
        
        # 创建新材质
        material = Material(
            name=material_name,
            pbrMetallicRoughness={
                "baseColorFactor": [0.125490203499794, 0.7921568751335144, 0.615686297416687, 1.0],
                "metallicFactor": 0.0,
                "roughnessFactor": 0.9997508525848389
            }
        )
        
        # 添加材质扩展
        material.extensions = {
            "KHR_materials_specular": {
                "specularFactor": 1.0,
                "specularColorFactor": [0.0004901961074210703, 0.0004901961074210703, 0.0004901961074210703]
            }
        }
        
        material_dict[material_name] = len(gltf.materials)
        gltf.materials.append(material)
        return material_dict[material_name]
    
    def get_product_extras(product):
        """获取产品的所有相关属性"""
        extras = {}
        
        # 获取所有直接属性
        def get_attribute_value(attr_value):
            if hasattr(attr_value, '__iter__') and not isinstance(attr_value, str):
                return [get_attribute_value(v) for v in attr_value]
            elif hasattr(attr_value, 'is_a'):
                return f"{attr_value.is_a()}_{attr_value.id()}"
            else:
                return str(attr_value)

        # 获取 Pset（属性集）中的属性
        def get_pset_properties(product):
            properties = {}
            if hasattr(product, 'IsDefinedBy'):
                for definition in product.IsDefinedBy:
                    if definition.is_a('IfcRelDefinesByProperties'):
                        property_set = definition.RelatingPropertyDefinition
                        if property_set.is_a('IfcPropertySet'):
                            for prop in property_set.HasProperties:
                                if prop.is_a('IfcPropertySingleValue'):
                                    if prop.NominalValue:
                                        properties[prop.Name] = str(prop.NominalValue.wrappedValue)
            return properties

        # 获取构件类型属性
        def get_type_properties(product):
            properties = {}
            if hasattr(product, 'IsTypedBy'):
                for rel in product.IsTypedBy:
                    if rel.is_a('IfcRelDefinesByType'):
                        product_type = rel.RelatingType
                        if hasattr(product_type, 'HasPropertySets'):
                            for pset in product_type.HasPropertySets:
                                if pset.is_a('IfcPropertySet'):
                                    for prop in pset.HasProperties:
                                        if prop.is_a('IfcPropertySingleValue') and prop.NominalValue:
                                            properties[prop.Name] = str(prop.NominalValue.wrappedValue)
            return properties

        # 基本属性
        if hasattr(product, "ObjectType"):
            extras["Class"] = product.ObjectType
        if hasattr(product, "Description"):
            extras["Reference"] = product.Description
        if hasattr(product, "Tag"):
            extras["Tag"] = product.Tag
        if hasattr(product, "Name"):
            extras["Name"] = product.Name
        if hasattr(product, "GlobalId"):
            extras["GlobalId"] = product.GlobalId

        # 获取材质信息
        if hasattr(product, "HasAssociations"):
            for association in product.HasAssociations:
                if association.is_a("IfcRelAssociatesMaterial"):
                    relating_material = association.RelatingMaterial
                    if relating_material.is_a("IfcMaterial"):
                        extras["Material"] = relating_material.Name
                    elif relating_material.is_a("IfcMaterialList"):
                        extras["Materials"] = [m.Name for m in relating_material.Materials]

        # 几何属性
        if hasattr(product, "Representation"):
            try:
                settings_volume = ifcopenshell.geom.settings()
                settings_volume.set(settings_volume.USE_WORLD_COORDS, True)
                shape = ifcopenshell.geom.create_shape(settings_volume, product)
                if shape:
                    extras["Volume"] = str(round(shape.geometry.volume, 3))
                    extras["Areapertons"] = str(round(shape.geometry.area, 1))
                    
                    # 获取包围盒信息
                    bbox = shape.geometry.bounding_box
                    if bbox:
                        extras["BoundingBox"] = {
                            "Min": [round(v, 3) for v in bbox.min],
                            "Max": [round(v, 3) for v in bbox.max]
                        }
            except:
                extras["Volume"] = "0"
                extras["Areapertons"] = "0"

        # 位置信息
        if hasattr(product, "ObjectPlacement"):
            try:
                placement = product.ObjectPlacement
                if placement:
                    if hasattr(placement, "RelativePlacement"):
                        rel_placement = placement.RelativePlacement
                        if hasattr(rel_placement, "Location") and rel_placement.Location:
                            coords = rel_placement.Location.Coordinates
                            extras["Position"] = {
                                "X": round(coords[0], 3),
                                "Y": round(coords[1], 3),
                                "Z": round(coords[2], 3)
                            }
                            extras["Bottomelevation"] = f"+{coords[2]:.3f}"
                            
                            # 尝试获取实际高度
                            if hasattr(product, "Representation"):
                                try:
                                    bbox = shape.geometry.bounding_box
                                    height = bbox.max[2] - bbox.min[2]
                                    extras["Topelevation"] = f"+{coords[2] + height:.3f}"
                                    extras["Height"] = str(round(height, 3))
                                except:
                                    extras["Topelevation"] = f"+{coords[2] + 0.1:.3f}"
                                    extras["Height"] = "0.1"
            except:
                pass

        # 获取 Pset 属性
        pset_props = get_pset_properties(product)
        extras.update(pset_props)

        # 获取类型属性
        type_props = get_type_properties(product)
        extras.update(type_props)

        # 获取数量属性
        if hasattr(product, "Quantity"):
            try:
                quantity_props = {}
                for quantity in product.Quantity:
                    if quantity.is_a('IfcQuantityLength'):
                        quantity_props[quantity.Name] = str(round(quantity.LengthValue, 3))
                    elif quantity.is_a('IfcQuantityArea'):
                        quantity_props[quantity.Name] = str(round(quantity.AreaValue, 3))
                    elif quantity.is_a('IfcQuantityVolume'):
                        quantity_props[quantity.Name] = str(round(quantity.VolumeValue, 3))
                extras.update(quantity_props)
            except:
                pass

        # 标准属性（如果没有从其他来源获取到）
        if "Length" not in extras:
            extras["Length"] = type_props.get("Length", "0")
        if "Width" not in extras:
            extras["Width"] = type_props.get("Width", "0")
        if "Height" not in extras and "Height" not in extras:
            extras["Height"] = type_props.get("Height", "0")
        if "Weight" not in extras:
            extras["Weight"] = type_props.get("Weight", "0")
        
        # 结构属性
        extras["LoadBearing"] = pset_props.get("LoadBearing", "T")
        extras["Phase"] = pset_props.get("Phase", "1")
        extras["Grossfootprintarea"] = pset_props.get("GrossFootprintArea", "0")
        extras["Netsurfacearea"] = pset_props.get("NetSurfaceArea", "0")
        extras["Preliminarymark"] = pset_props.get("PreliminaryMark", "")

        return extras
    
    def process_geometry(shape):
        """处理几何数据"""
        vertices = np.array(shape.geometry.verts).reshape(-1, 3)
        faces = np.array(shape.geometry.faces)
        
        # 检查是否有有效的顶点数据
        if len(vertices) == 0:
            raise ValueError("No vertex data found")
        
        # 获取法线数据
        if hasattr(shape.geometry, 'normals') and len(shape.geometry.normals) > 0:
            normals = np.array(shape.geometry.normals).reshape(-1, 3)
        else:
            # 如果没有法线或法线为空，计算法线
            # 为每个顶点创建法线数组
            normals = np.zeros_like(vertices)
            if len(faces) > 0:  # 确保有面数据
                # 创建一个临时数组来累积每个顶点的法线
                vertex_normals = np.zeros_like(vertices)
                vertex_counts = np.zeros(len(vertices), dtype=np.int32)
                
                # 遍历每个三角形
                for i in range(0, len(faces), 3):
                    if i + 2 < len(faces):  # 确保有足够的索引
                        try:
                            # 获取三角形的三个顶点
                            v1_idx = faces[i]
                            v2_idx = faces[i + 1]
                            v3_idx = faces[i + 2]
                            
                            # 确保索引在有效范围内
                            if max(v1_idx, v2_idx, v3_idx) < len(vertices):
                                v1 = vertices[v1_idx]
                                v2 = vertices[v2_idx]
                                v3 = vertices[v3_idx]
                                
                                # 计算面法线
                                normal = np.cross(v2 - v1, v3 - v1)
                                norm = np.linalg.norm(normal)
                                if norm > 0:  # 避免除以零
                                    normal = normal / norm
                                else:
                                    normal = np.array([0.0, 1.0, 0.0])
                                
                                # 将法线添加到每个顶点
                                vertex_normals[v1_idx] += normal
                                vertex_normals[v2_idx] += normal
                                vertex_normals[v3_idx] += normal
                                vertex_counts[v1_idx] += 1
                                vertex_counts[v2_idx] += 1
                                vertex_counts[v3_idx] += 1
                        except Exception as e:
                            print(f"Warning: Error calculating normal for face {i//3}: {str(e)}")
                            continue
                
                # 计算每个顶点的平均法线
                for i in range(len(vertices)):
                    if vertex_counts[i] > 0:
                        normals[i] = vertex_normals[i] / vertex_counts[i]
                        # 标准化法线
                        norm = np.linalg.norm(normals[i])
                        if norm > 0:
                            normals[i] = normals[i] / norm
                        else:
                            normals[i] = np.array([0.0, 1.0, 0.0])
                    else:
                        normals[i] = np.array([0.0, 1.0, 0.0])
            else:
                # 如果没有面数据，所有法线默认向上
                normals[:] = np.array([0.0, 1.0, 0.0])
        
        # 确保数据精度并检查数据有效性
        vertices = vertices.astype(np.float32)
        normals = normals.astype(np.float32)
        faces = faces.astype(np.uint32)
        
        # 确保法线数组不为空且大小正确
        if len(normals) != len(vertices):
            normals = np.tile(np.array([0.0, 1.0, 0.0], dtype=np.float32), (len(vertices), 1))
        
        # 验证数据
        if len(vertices) == 0 or len(normals) == 0 or len(faces) == 0:
            raise ValueError("Invalid geometry data")
        
        return vertices, normals, faces
    
    # 用于存储构件信息的扩展
    component_info = {}
    
    # 遍历IFC文件中的所有产品
    for product in ifc_file.by_type("IfcProduct"):
        if not product.Representation:
            continue

        try:
            # 处理形状
            shape = ifcopenshell.geom.create_shape(settings, product)
            
            if shape:
                # 处理几何数据
                vertices, normals, indices = process_geometry(shape)
                
                # 创建buffer数据
                vertex_data = vertices.tobytes()
                normal_data = normals.tobytes()
                index_data = indices.tobytes()
                
                # 添加到总buffer
                vertex_offset = current_buffer_length
                all_buffer_data.extend(vertex_data)
                current_buffer_length += len(vertex_data)
                
                normal_offset = current_buffer_length
                all_buffer_data.extend(normal_data)
                current_buffer_length += len(normal_data)
                
                index_offset = current_buffer_length
                all_buffer_data.extend(index_data)
                current_buffer_length += len(index_data)
                
                # 创建bufferViews
                vertex_buffer_view = BufferView(
                    buffer=0,
                    byteOffset=vertex_offset,
                    byteLength=len(vertex_data),
                    target=34962  # ARRAY_BUFFER
                )
                normal_buffer_view = BufferView(
                    buffer=0,
                    byteOffset=normal_offset,
                    byteLength=len(normal_data),
                    target=34962  # ARRAY_BUFFER
                )
                index_buffer_view = BufferView(
                    buffer=0,
                    byteOffset=index_offset,
                    byteLength=len(index_data),
                    target=34963  # ELEMENT_ARRAY_BUFFER
                )
                gltf.bufferViews.extend([vertex_buffer_view, normal_buffer_view, index_buffer_view])
                
                # 创建accessors
                vertex_accessor = Accessor(
                    bufferView=len(gltf.bufferViews) - 3,
                    componentType=5126,  # FLOAT
                    count=len(vertices),
                    type="VEC3",
                    max=convert_numpy_types(vertices.max(axis=0)),
                    min=convert_numpy_types(vertices.min(axis=0))
                )
                normal_accessor = Accessor(
                    bufferView=len(gltf.bufferViews) - 2,
                    componentType=5126,  # FLOAT
                    count=len(normals),
                    type="VEC3",
                    max=convert_numpy_types(normals.max(axis=0)),
                    min=convert_numpy_types(normals.min(axis=0))
                )
                index_accessor = Accessor(
                    bufferView=len(gltf.bufferViews) - 1,
                    componentType=5125,  # UNSIGNED_INT
                    count=len(indices),
                    type="SCALAR",
                    max=[convert_numpy_types(indices.max())],
                    min=[convert_numpy_types(indices.min())]
                )
                gltf.accessors.extend([vertex_accessor, normal_accessor, index_accessor])
                
                # 创建primitive
                primitive = Primitive(
                    attributes={
                        "POSITION": len(gltf.accessors) - 3,
                        "NORMAL": len(gltf.accessors) - 2
                    },
                    indices=len(gltf.accessors) - 1,
                    material=create_material_with_extensions(product)
                )
                
                # 创建mesh
                mesh = Mesh(primitives=[primitive])
                gltf.meshes.append(mesh)
                
                # 创建node
                node = Node(
                    mesh=len(gltf.meshes) - 1,
                    name=f"{product.is_a()}_{product.GlobalId}"
                )
                
                # 添加变换矩阵（如果有）
                if hasattr(product, "ObjectPlacement"):
                    try:
                        matrix = convert_numpy_types(shape.transformation.matrix.flatten())
                        node.matrix = matrix
                    except:
                        pass
                
                # 添加额外信息
                node.extras = get_product_extras(product)
                
                gltf.nodes.append(node)
                
                # 存储构件信息
                component_info[str(len(gltf.nodes) - 1)] = {
                    "globalId": product.GlobalId,
                    "type": product.is_a(),
                    "name": product.Name if hasattr(product, "Name") else None
                }
                
                print(f"成功: 已处理 {product.is_a()} (GlobalId: {product.GlobalId})")
                
        except RuntimeError as e:
            print(f"警告: 处理产品 {product.is_a()} (GlobalId: {product.GlobalId}) 时出错: {str(e)}")
            continue
    
    # 将几何数据写入.bin文件
    with open(bin_file_path, 'wb') as f:
        f.write(all_buffer_data)
    
    # 创建指向外部.bin文件的buffer
    buffer = Buffer(
        byteLength=len(all_buffer_data),
        uri=os.path.basename(bin_file_path)  # 使用相对路径
    )
    gltf.buffers.append(buffer)
    
    # 创建scene
    scene = Scene(nodes=list(range(len(gltf.nodes))))
    gltf.scenes.append(scene)
    gltf.scene = 0
    
    # 添加扩展信息
    gltf.extras = {
        "components": component_info
    }
    
    # 在保存之前进行最后的类型转换
    gltf = convert_all_numpy_in_gltf(gltf)
    gltf.save(gltf_file_path)
    print(f"转换完成！")
    print(f"GLTF文件已保存为: {gltf_file_path}")
    print(f"几何数据已保存为: {bin_file_path}")

if __name__ == "__main__":
    # 转换文件
    import time
    ifc_file_path = "1234.ifc"
    gltf_file_path = "1234_bin_gltf.gltf"
    start_time = time.time()
    ifc_to_gltf(ifc_file_path, gltf_file_path)
    end_time = time.time()
    print(f"转换完成！用时: {end_time - start_time:.2f}秒")

# import sys
# print(sys.path)

# import ifcopenshell
# print(ifcopenshell.version)
# model = ifcopenshell.file()