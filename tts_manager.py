import json
import os
import shutil
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional
import random
import matplotlib.pyplot as plt
import matplotlib.image as mpimg


# ===== 可置顶修改的变量 =====
DEFAULT_CONFIG = {
    "group_threshold_minutes": 60,
    "move_deleted_files_to_backup": True,
}
EXCLUDED_TOP_LEVEL_FOLDERS = {"Images Raw", "Models Raw"}

DATA_FILE_NAME = "tts_manager_data.json"
BACKUP_DIR_NAME = "deleted_files_backup"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}

@dataclass
class FileInfo:
    path: str
    created_at: float
    top_level_folder: str


@dataclass
class GroupInfo:
    id: int
    name: str
    created_at: float
    files: list[FileInfo]


def script_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = script_dir()
DATA_PATH = BASE_DIR / DATA_FILE_NAME
BACKUP_DIR = BASE_DIR / BACKUP_DIR_NAME


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def format_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def load_data() -> dict:
    if not DATA_PATH.exists():
        return {}

    try:
        with DATA_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        print(f"警告：读取 {DATA_PATH.name} 失败，将使用空数据。")
        return {}


def save_data(data: dict) -> None:
    data["saved_at"] = now_text()

    with DATA_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_config() -> dict:
    data = load_data()
    config = data.get("config", {})

    changed = False

    for key, default_value in DEFAULT_CONFIG.items():
        if key not in config:
            config[key] = default_value
            changed = True

    if changed or "config" not in data:
        data["config"] = config
        save_data(data)

    return config


def get_config() -> dict:
    config = load_config()
    return {
        "group_threshold_minutes": int(
            config.get(
                "group_threshold_minutes",
                DEFAULT_CONFIG["group_threshold_minutes"],
            )
        ),
        "move_deleted_files_to_backup": config.get(
            "move_deleted_files_to_backup",
            DEFAULT_CONFIG["move_deleted_files_to_backup"],
        ),
    }


def normalize_mods_path(raw_path: str) -> Optional[Path]:
    raw_path = raw_path.strip().strip('"')

    if not raw_path:
        return None
    try:
        path = Path(raw_path).expanduser().resolve()
    except Exception:
        return None

    if not path.is_dir():
        return None

    if path.name == "Mods":
        mods_path = path
    elif path.name == "Tabletop Simulator_Data":
        mods_path = path / "Mods"
    else:
        mods_path = path / "Tabletop Simulator_Data" / "Mods"

    return mods_path if mods_path.is_dir() else None


def ask_mods_path() -> Path:
    while True:
        raw = input(
            "请输入路径，可为Tabletop Simulator、Tabletop Simulator_Data 或 Mods 文件夹："
        )

        mods_path = normalize_mods_path(raw)

        if mods_path is None:
            print(
                "路径无效。只接受以下三种路径：\n"
                "  xxx\\\n"
                "  xxx\\Tabletop Simulator_Data\n"
                "  xxx\\Tabletop Simulator_Data\\Mods"
            )
            continue

        data = load_data()
        data["mods_path"] = str(mods_path)
        save_data(data)

        return mods_path


def get_mods_path() -> Path:
    saved = load_data().get("mods_path")

    if saved:
        mods_path = normalize_mods_path(saved)

        if mods_path is not None:
            return mods_path

        print("已保存的 Mods 路径无效，需要重新设置。")

    return ask_mods_path()


def reset_mods_path() -> Path:
    print("重设 Tabletop Simulator Mods 路径。")
    return ask_mods_path()


def get_creation_time(path: Path) -> float:
    """
    Windows 上 st_ctime 通常是创建时间。
    macOS / Linux 上 st_ctime 是元数据变化时间，不一定是真正创建时间。
    """
    return path.stat().st_ctime


def scan_files(mods_path: Path) -> list[FileInfo]:
    files: list[FileInfo] = []

    for top in mods_path.iterdir():
        if not top.is_dir() or top.name in EXCLUDED_TOP_LEVEL_FOLDERS:
            continue

        for item in top.rglob("*"):
            if not item.is_file():
                continue

            try:
                files.append(
                    FileInfo(
                        path=str(item.resolve()),
                        created_at=get_creation_time(item),
                        top_level_folder=top.name,
                    )
                )
            except OSError:
                print(f"警告：无法读取文件信息，已跳过：{item}")

    return sorted(files, key=lambda f: f.created_at)


def make_group(group_id: int, files: list[FileInfo]) -> GroupInfo:
    return GroupInfo(
        id=group_id,
        name="",
        created_at=min(f.created_at for f in files),
        files=files,
    )


def build_groups(files: list[FileInfo]) -> list[GroupInfo]:
    if not files:
        return []

    threshold = get_config()["group_threshold_minutes"] * 60
    groups: list[GroupInfo] = []
    current = [files[0]]

    for file in files[1:]:
        # 要求：当前分组最早文件 到 新文件 的时差不超过阈值
        if file.created_at - current[0].created_at <= threshold:
            current.append(file)
        else:
            groups.append(make_group(len(groups) + 1, current))
            current = [file]

    groups.append(make_group(len(groups) + 1, current))
    return groups


def group_to_dict(group: GroupInfo) -> dict:
    return {
        "id": group.id,
        "name": group.name,
        "created_at": group.created_at,
        "created_at_text": format_time(group.created_at),
        "file_count": len(group.files),
        "files": [asdict(f) for f in group.files],
    }


def group_from_dict(raw: dict) -> GroupInfo:
    return GroupInfo(
        id=int(raw["id"]),
        name=raw.get("name", ""),
        created_at=float(raw["created_at"]),
        files=[
            FileInfo(
                path=f["path"],
                created_at=float(f["created_at"]),
                top_level_folder=f.get("top_level_folder", ""),
            )
            for f in raw.get("files", [])
        ],
    )


def load_groups() -> list[GroupInfo]:
    return [group_from_dict(g) for g in load_data().get("groups", [])]


def save_groups(groups: list[GroupInfo]) -> None:
    data = load_data()
    data["groups"] = [group_to_dict(g) for g in groups]
    save_data(data)


def file_identity(file: FileInfo) -> str:
    return os.path.normcase(os.path.abspath(file.path))


def carry_names(old_groups: list[GroupInfo], new_groups: list[GroupInfo]) -> None:
    named_old = [g for g in old_groups if g.name.strip()]
    used_old_ids = set()

    for new in new_groups:
        new_files = {file_identity(f) for f in new.files}
        best: Optional[GroupInfo] = None
        best_overlap = 0

        for old in named_old:
            if old.id in used_old_ids:
                continue

            overlap = len(new_files & {file_identity(f) for f in old.files})
            if overlap > best_overlap:
                best = old
                best_overlap = overlap

        if best and best_overlap > 0:
            new.name = best.name
            used_old_ids.add(best.id)


def refresh_groups(
    mods_path: Path,
    old_groups: Optional[list[GroupInfo]] = None,
) -> list[GroupInfo]:
    groups = build_groups(scan_files(mods_path))

    if old_groups:
        carry_names(old_groups, groups)

    save_groups(groups)
    return groups


def find_group(groups: list[GroupInfo], group_id: int) -> Optional[GroupInfo]:
    return next((g for g in groups if g.id == group_id), None)


def input_group_id(groups: list[GroupInfo]) -> Optional[int]:
    raw = input("请输入分组编号：").strip()

    if not raw.isdigit():
        print("分组编号必须是数字。")
        return None

    group_id = int(raw)

    if not find_group(groups, group_id):
        print("没有找到该分组。")
        return None

    return group_id


def display_groups(groups: list[GroupInfo]) -> None:
    print("\n========== 当前分组 ==========")

    if not groups:
        print("没有找到可分组文件。")
    else:
        for group in groups:
            name = group.name.strip() or "（空白）"
            print(
                f"[{group.id}] 名称：{name} | "
                f"创建时间：{format_time(group.created_at)} | "
                f"文件数：{len(group.files)}"
            )

    print("==============================")
    print(f"文件路径：{get_mods_path()}\n")


def show_images_in_group(group: GroupInfo) -> None:
    image_files = []

    for file in group.files:
        path = Path(file.path)
        if not path.suffix.lower() in IMAGE_EXTENSIONS:
            continue
        image_files.append(path)

    if not image_files:
        print("该分组中没有找到图片文件。")
        return

    selected = random.sample(image_files, min(10, len(image_files)))
    _, axes = plt.subplots(2, 5, figsize=(10, 4))
    axes = axes.flatten()

    for ax in axes:
        ax.axis("off")

    for ax, image_path in zip(axes, selected):
        try:
            img = mpimg.imread(image_path)
            ax.imshow(img)
        except Exception as exc:
            print(f"图片读取失败：{image_path} | {exc}")
    plt.tight_layout()
    plt.show(block=False)

def display_files(groups: list[GroupInfo]) -> None:
    group_id = input_group_id(groups)
    if group_id is None:
        return

    group = find_group(groups, group_id)
    
    show_images_in_group(group)

    print("========== 图片文件 ===========")
    count = 0
    for file in group.files:
        if Path(file.path).suffix.lower() in IMAGE_EXTENSIONS:
            count += 1
            if count > 20:
                print("...")
                break
            print(f"[{count}] {file.path}")
    print("========== 其他文件 ===========")
    count = 0
    for file in group.files:
        if Path(file.path).suffix.lower() in IMAGE_EXTENSIONS | {".obj", ".fbx"}:
            continue
        count += 1
        if count > 20:
            print("...")
            break
        print(f"[{count}] {file.path}")
    print("================================\n")


def rename_group(groups: list[GroupInfo]) -> None:
    group_id = input_group_id(groups)
    if group_id is None:
        return

    group = find_group(groups, group_id)
    if not group:
        return

    group.name = input("请输入新的分组名称，留空表示清除名称：").strip()
    save_groups(groups)
    print("已保存分组名称。")


def safe_backup_path(original: Path) -> Path:
    parts = original.resolve().parts
    mods_index = parts.index("Mods")
    return BACKUP_DIR / Path(*parts[mods_index:])


def delete_file(path: Path) -> bool:
    try:
        if get_config()["move_deleted_files_to_backup"]:
            target = safe_backup_path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
        else:
            path.unlink()

        return True
    except Exception as exc:
        print(f"删除失败：{path} | {exc}")
        return False


def delete_group_files(groups: list[GroupInfo]) -> list[GroupInfo]:
    group_id = input_group_id(groups)
    if group_id is None:
        return groups

    group = find_group(groups, group_id)
    if not group:
        return groups

    name = group.name.strip() or "（空白）"
    print(f"将删除分组 [{group.id}] 名称：{name}，文件数：{len(group.files)}")

    confirm = input(
        "确认删除该分组内所有文件吗？此操作不可轻易撤销。(输入 DELETE 确认)："
    ).strip()

    if confirm.upper() != "DELETE":
        print("已取消删除。")
        return groups

    deleted = failed = 0

    for file in group.files:
        path = Path(file.path)
        if not path.is_file():
            continue

        if delete_file(path):
            deleted += 1
        else:
            failed += 1

    print(f"删除完成。成功：{deleted}，失败：{failed}")

    remaining_old_groups = [g for g in groups if g.id != group_id]
    return refresh_groups(get_mods_path(), remaining_old_groups)


def show_menu() -> None:
    print("可用命令：")
    print("  v / view      查看文件")
    print("  n / name      命名分组")
    print("  d / delete    删除分组")
    print("  r / refresh   刷新列表")
    print("  p / path      重设路径")
    print("  q / quit      退出\n")


def main() -> None:
    print("Tabletop Simulator Mod Manager")
    print("https://github.com/ZHXSpaceProgram/tts_manager")
    print()

    mods_path = get_mods_path()
    groups = refresh_groups(mods_path, load_groups())

    while True:
        display_groups(groups)
        show_menu()
        command = input("请输入命令：").strip().lower()

        if command in ("q", "quit"):
            print("退出。")
            break

        elif command in ("v", "view"):
            display_files(groups)

        elif command in ("n", "name"):
            rename_group(groups)
        
        elif command in ("d", "delete"):
            groups = delete_group_files(groups)

        elif command in ("r", "refresh"):
            groups = refresh_groups(get_mods_path(), groups)
            print("已刷新分组，并已自动保存。")

        elif command in ("p", "path"):
            mods_path = reset_mods_path()
            groups = refresh_groups(mods_path, groups)
            print("路径已重设，并已刷新分组。")

        else:
            print("未知命令，请输入菜单中的命令。\n")

        input("按 Enter 键继续...")


if __name__ == "__main__":
    main()