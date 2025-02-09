"""
这是一个可高度定制化的 PPQ 程序入口脚本

与 ProgramEntrance_1 相比，新的 API 允许你控制量化的所有细节。

你可以：

    * 自定义与注册新的量化器
    
    * 干预网络调度逻辑
    
    * 在量化之前修改网络结构
    
    * 自定义优化过程，并控制量化管线

"""

import os

import numpy as np
import torch
import torchvision

import ppq.lib as PFL
from ppq import TargetPlatform, TorchExecutor, graphwise_error_analyse
from ppq.api import ENABLE_CUDA_KERNEL, export_ppq_graph, load_torch_model
from ppq.quantization.optim import *

# 你需要自己写一个函数来加载数据
calibration_dataloader = []
for file in os.listdir('imagenet'):
    path = os.path.join('imagenet', file)
    arr  = np.fromfile(path, dtype=np.dtype('float32')).reshape([1, 3, 224, 224])
    calibration_dataloader.append(torch.tensor(arr))

with ENABLE_CUDA_KERNEL():
    model = torchvision.models.resnet18(pretrained=True).cuda()
    graph = load_torch_model(model=model, sample=torch.zeros(size=[1, 3, 224, 224]).cuda())

    quantizer   = PFL.Quantizer(platform=TargetPlatform.TRT_INT8, graph=graph) # 取得 TRT_INT8 所对应的量化器
    dispatching = PFL.Dispatcher(graph=graph).dispatch(                        # 生成调度表
        quant_types=quantizer.quant_operation_types)

    # 为算子初始化量化信息
    for op in graph.operations.values():
        quantizer.quantize_operation(
            op_name = op.name, platform = dispatching[op.name])

    # 初始化执行器
    collate_fn = lambda x: x.to('cuda')
    executor = TorchExecutor(graph=graph, device='cuda')
    executor.tracing_operation_meta(inputs=torch.zeros(size=[1, 3, 224, 224]).cuda())
    executor.load_graph(graph=graph)

    # 创建优化管线，在 ProgramEntrance_1.py 中，我们使用 QuantizationSetting 创建优化管线
    # 而在这里，所有的一切需要你手动创建
    pipeline = PFL.Pipeline([
        QuantizeSimplifyPass(),
        QuantizeFusionPass(
            activation_type=quantizer.activation_fusion_types),
        ParameterQuantizePass(),
        RuntimeCalibrationPass(),
        PassiveParameterQuantizePass(),
        QuantAlignmentPass(force_overlap=True),

        # 微调你的网路
        # LearnedStepSizePass(steps=1500)

        # 如果需要训练微调网络，训练过程必须发生在 ParameterBakingPass 之前
        # ParameterBakingPass()

        # 启动保序校准，在 graphwise_error_analyse 中保序校准精度可能低于正常的 percentile 校准
        # 但这一校准方法是专为分类变量设计的，它的分类精度应当更高
        # IsotoneCalibrationPass(variables=[name for name in graph.outputs])
    ])

    # 调用管线完成量化
    pipeline.optimize(
        graph=graph, dataloader=calibration_dataloader, verbose=True, 
        calib_steps=32, collate_fn=collate_fn, executor=executor)

    graphwise_error_analyse(
        graph=graph, running_device='cuda', dataloader=calibration_dataloader, 
        collate_fn=lambda x: x.cuda())

    export_ppq_graph(
        graph=graph, platform=TargetPlatform.TRT_INT8, 
        graph_save_to='Output/quantized.onnx', 
        config_save_to='Output/quantized.json')
