# -*- coding: utf-8 -*-
"""功能二：把一组标准化动作组装成桌宠角色包（皮肤）。

角色包结构（放到 desktop-pet/skins/<角色id>/ 即可被桌宠识别）：

  <角色id>/
    role.json              # 角色元数据（必需）
    prompt.txt             # 可选：该角色专属人设提示词（覆盖当前人格 prompt 全文）
    <动作key>/             # 每个动作一个子目录，目录名 = 桌宠内部动作 key
      frame_0000.png ...
      traj.json            # 位移动作可选

role.json 字段：
  id          角色 id（英文/数字，唯一，也是文件夹名）
  name        角色显示名
  author      作者（可选）
  version     版本号
  default_pet_name   宠物默认名（切换皮肤后 AI 称呼跟随）
  prompt_file 可选：皮肤自带 prompt 文件名（默认 prompt.txt）
  actions     {动作key: {label: 显示名, dir: 帧图目录名}}，帧图目录名默认=动作key
  action_tags 可选：覆盖 AI 输出规范里的 {动作标签词}，如 {"思考":"think"}
  chatlines   可选：覆盖随机台词库（待机/点击/拖动等卖萌台词）
"""
import os
import json
import shutil


class PackageError(Exception):
    """打包过程中的用户可读错误。"""


# 桌宠内置动作 key → 默认显示名（打包时若用户不填 label 用此）
DEFAULT_ACTION_LABELS = {
    "idle": "坐地上生气",
    "front": "正面",
    "back": "背面",
    "side": "侧面",
    "sit": "坐下",
    "rise": "起身",
    "turn": "转向",
    "angry": "生气",
    "think": "思考",
    "happy": "开心",
    "cry": "哭泣",
    "shy": "害羞",
    "surprised": "惊讶",
    "sleep": "站着睡着",
    "jump": "跳跃",
    "roll": "打滚",
}

# 桌宠内置动作标签词（prompt 输出规范里的 {词}），皮肤可覆盖
DEFAULT_ACTION_TAGS = {
    "待机": "idle",
    "思考": "think",
    "开心": "happy",
    "害羞": "shy",
    "兴奋": "front",
    "挥手": "front",
    "惊讶": "surprised",
    "审阅": "front",
    "跳跃": "jump",
    "睡眠": "sleep",
}


def find_frames(src_dir):
    """扫描源动作目录里的 PNG 帧图（按数字排序），返回文件名列表。"""
    if not os.path.isdir(src_dir):
        raise PackageError(f"动作目录不存在：{src_dir}")
    files = sorted(
        f for f in os.listdir(src_dir) if f.lower().endswith(".png"))
    if not files:
        raise PackageError(f"动作目录里没有 PNG 帧图：{src_dir}")
    return files


def copy_traj(src_dir, dst_dir):
    """若源目录含 traj.json 则复制到目标（位移动作）。"""
    tp = os.path.join(src_dir, "traj.json")
    if os.path.isfile(tp):
        shutil.copy2(tp, os.path.join(dst_dir, "traj.json"))
        return True
    return False


def build_role(role_id, name, author, version, default_pet_name,
               actions, action_tags=None, chatlines=None, prompt_text=None):
    """构造 role.json 字典。actions 为 [(key, label, src_dir), ...]。"""
    role = {
        "id": role_id,
        "name": name or role_id,
        "author": author or "",
        "version": version or "1.0.0",
        "default_pet_name": default_pet_name or name or role_id,
        "actions": {},
    }
    if prompt_text is not None and prompt_text.strip():
        role["prompt_file"] = "prompt.txt"
    if action_tags:
        role["action_tags"] = dict(action_tags)
    if chatlines:
        role["chatlines"] = dict(chatlines)

    for key, label, _src in actions:
        role["actions"][key] = {
            "label": label or DEFAULT_ACTION_LABELS.get(key, key),
        }
    return role


def package_role(role_id, name, author, version, default_pet_name,
                 actions, out_dir, action_tags=None, chatlines=None,
                 prompt_text=None, progress=None):
    """把动作列表组装成角色包，写入 out_dir/<role_id>/。

    actions: [(动作key, 显示label, 源帧图目录), ...]
    out_dir: 角色包输出根目录（桌宠端即 desktop-pet/skins/）
    返回 role.json 路径。
    """
    if not role_id or not str(role_id).strip():
        raise PackageError("角色 id 不能为空（建议英文/数字，如 dafeiyu）")
    if not re_ok(str(role_id)):
        raise PackageError("角色 id 只能包含字母、数字、下划线、中划线")
    if not actions:
        raise PackageError("至少需要一个动作")

    dst = os.path.join(out_dir, str(role_id).strip())
    os.makedirs(dst, exist_ok=True)

    # 复制帧图
    for i, (key, label, src) in enumerate(actions):
        if not key or not re_ok(str(key)):
            raise PackageError(f"动作 key 非法：{key}（只能含字母数字下划线中划线）")
        files = find_frames(src)
        adir = os.path.join(dst, str(key))
        os.makedirs(adir, exist_ok=True)
        for fn in files:
            shutil.copy2(os.path.join(src, fn), os.path.join(adir, fn))
        has_traj = copy_traj(src, adir)
        if progress:
            progress(i + 1, len(actions),
                     f"[{i + 1}/{len(actions)}] {key}：{len(files)} 帧"
                     + (" + traj" if has_traj else ""))
        else:
            print(f"  - {key}: {len(files)} 帧{' + traj' if has_traj else ''}")

    # 皮肤自带 prompt（可选）
    if prompt_text is not None and prompt_text.strip():
        with open(os.path.join(dst, "prompt.txt"), "w", encoding="utf-8") as f:
            f.write(prompt_text.strip() + "\n")

    # role.json
    role = build_role(role_id, name, author, version, default_pet_name,
                      actions, action_tags=action_tags, chatlines=chatlines,
                      prompt_text=prompt_text)
    rp = os.path.join(dst, "role.json")
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(role, f, ensure_ascii=False, indent=2)

    if progress:
        progress(len(actions), len(actions), f"角色包完成：{dst}")
    return rp


def re_ok(s):
    """id 类字段合法性：字母数字下划线中划线。"""
    return all(c.isalnum() or c in "_-" for c in s)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 5:
        print("用法：python package_role.py 角色id 角色名 输出目录 动作key:帧图目录 ...")
        sys.exit(1)
    acts = []
    for a in sys.argv[4:]:
        if ":" in a:
            k, d = a.split(":", 1)
        else:
            k, d = a, a
        acts.append((k, None, d))
    try:
        rp = package_role(sys.argv[1], sys.argv[2], "", "1.0.0",
                          sys.argv[2], acts, sys.argv[3])
        print(f"OK：{rp}")
    except PackageError as e:
        print(f"错误：{e}")
        sys.exit(1)