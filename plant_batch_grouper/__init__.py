# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

bl_info = {'name': '植物批量分组 Plant Batch Grouper', 'author': 'haoccy0u / Codex',
           'version': (2, 3, 2), 'blender': (4, 2, 0), 'location': '3D 视图 > N > 植物分组',
           'description': '草本分组、分批合并、根部原点与 +X 生长方向准备', 'category': 'Object'}
from .ui import register, unregister
