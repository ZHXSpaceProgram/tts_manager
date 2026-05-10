import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
import random
from PIL import Image, ImageTk
import tkinter as tk

# ===== 可置顶修改的变量 =====
PREVIEW_IMAGE_COUNT = 15
DEFAULT_CONFIG = {
    "move_deleted_files_to_backup": True,
}
DATA_FILE_NAME = "tts_manager_data.json"
BACKUP_DIR_NAME = "deleted_files_backup"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
IMAGE_WINDOWS = []  # 用于保持图片窗口的引用，防止被垃圾回收


@dataclass
class FileInfo:
    path: str
    top_level_folder: str


@dataclass
class GroupInfo:
    id: int
    name: str
    json_path: str
    files: list[FileInfo] = field(default_factory=list)


def script_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = script_dir()
DATA_PATH = BASE_DIR / DATA_FILE_NAME
BACKUP_DIR = BASE_DIR / BACKUP_DIR_NAME


# region Time Handling ===================================================================
def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
# endregion


# region Data Handling ==================================================================
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
    raw_config = data.get("config", {})
    config = {
        key: raw_config.get(key, default_value)
        for key, default_value in DEFAULT_CONFIG.items()
    }

    if data.get("config") != config:
        data["config"] = config
        save_data(data)

    return config


def to_bool(value):
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.strip().lower() == "true"

    return bool(value)


def get_config() -> dict:
    config = load_config()
    return {
        "move_deleted_files_to_backup": to_bool(
            config.get(
                "move_deleted_files_to_backup",
                DEFAULT_CONFIG["move_deleted_files_to_backup"],
            )
        ),
    }
# endregion


# region Mods Path Handling ==================================================================
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
        raw = input("请输入 Tabletop Simulator_Data 路径：")
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
# endregion


# region File Helpers ==================================================================
def file_identity(file: FileInfo | Path | str) -> str:
    if isinstance(file, FileInfo):
        raw_path = file.path
    else:
        raw_path = str(file)
    return os.path.normcase(os.path.abspath(raw_path))


def safe_folder_name(text: str) -> str:
    text = text.strip() or "unnamed"
    invalid = '<>:"/\\|?*'
    for ch in invalid:
        text = text.replace(ch, "_")
    return text


def path_contains_workshop(path: Path) -> bool:
    return any(part.lower() == "workshop" for part in path.parts)


def scan_files(mods_path: Path) -> list[FileInfo]:
    files: list[FileInfo] = []

    for top in sorted(mods_path.iterdir(), key=lambda p: p.name.lower()):
        if not top.is_dir():  # 包含缓存文件
            continue

        for item in sorted(top.rglob("*"), key=lambda p: str(p).lower()):
            if not item.is_file():
                continue

            try:
                files.append(
                    FileInfo(
                        path=str(item.resolve()),
                        top_level_folder=top.name,
                    )
                )
            except OSError:
                print(f"警告：无法读取文件信息，已跳过：{item}")

    return files


def find_workshop_json_files(files: list[FileInfo]) -> list[Path]:
    json_files = [
        Path(file.path)
        for file in files
        if Path(file.path).suffix.lower() == ".json"
        and Path(file.path).name != "WorkshopFileInfos.json"
        and path_contains_workshop(Path(file.path))
    ]
    return sorted(json_files, key=lambda p: str(p).lower())


def index_files_by_stem(files: list[FileInfo]) -> dict[str, list[FileInfo]]:
    index: dict[str, list[FileInfo]] = {}

    for file in files:
        path = Path(file.path)
        index.setdefault(path.stem, []).append(file)

    return index


def url_to_cache_stem(url: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", url)


def extract_urls_from_text(text: str) -> list[str]:
    normalized_text = text.replace(r"\/", "/")
    urls = re.findall(r"https?://[^\s\"'<>]+", normalized_text)

    result: list[str] = []
    seen: set[str] = set()

    for url in urls:
        clean_url = url.rstrip("),.;]")
        if clean_url and clean_url not in seen:
            result.append(clean_url)
            seen.add(clean_url)

    return result


def find_same_name_workshop_image(json_path: Path) -> Optional[Path]:
    if not path_contains_workshop(json_path) or not json_path.parent.is_dir():
        return None

    for item in json_path.parent.iterdir():
        if not item.is_file():
            continue
        if item.stem == json_path.stem and item.suffix.lower() in IMAGE_EXTENSIONS:
            return item.resolve()

    return None


def add_file_once(files: list[FileInfo], file: FileInfo, seen_paths: set[str]) -> None:
    identity = file_identity(file)
    if identity in seen_paths:
        return
    files.append(file)
    seen_paths.add(identity)


def top_level_folder_from_path(mods_path: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(mods_path.resolve())
    except ValueError:
        return ""
    return relative.parts[0] if relative.parts else ""


def file_info_from_path(mods_path: Path, path: Path) -> FileInfo:
    return FileInfo(
        path=str(path.resolve()),
        top_level_folder=top_level_folder_from_path(mods_path, path),
    )


def mods_relative_path(original: Path) -> Path:
    parts = original.resolve().parts

    for index in range(len(parts) - 1, -1, -1):
        if parts[index].lower() == "mods":
            return Path(*parts[index:])

    return Path(original.name)


def make_backup_group_dir(group: GroupInfo) -> Path:
    group_name = safe_folder_name(group.name or Path(group.json_path).stem)
    delete_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    group_folder = f"group{group.id}_{group_name}_{delete_time}"

    return BACKUP_DIR / group_folder


def safe_backup_path(original: Path, backup_group_dir: Path) -> Path:
    return backup_group_dir / mods_relative_path(original)
# endregion


# region Grouping Logic ==================================================================
def make_group(
    group_id: int,
    json_path: Path,
    file_index: dict[str, list[FileInfo]],
    mods_path: Path,
) -> GroupInfo:
    group_files: list[FileInfo] = []
    seen_paths: set[str] = set()

    add_file_once(group_files, file_info_from_path(mods_path, json_path), seen_paths)

    cover_image = find_same_name_workshop_image(json_path)
    if cover_image is not None:
        add_file_once(group_files, file_info_from_path(mods_path, cover_image), seen_paths)

    try:
        text = json_path.read_text(encoding="utf-8-sig", errors="ignore")
    except Exception as exc:
        print(f"警告：读取 Workshop JSON 失败，已跳过 URL 收集：{json_path} | {exc}")
        text = ""

    for url in extract_urls_from_text(text):
        stem = url_to_cache_stem(url)
        for file in file_index.get(stem, []):
            add_file_once(group_files, file, seen_paths)

    return GroupInfo(
        id=group_id,
        name="",
        json_path=str(json_path.resolve()),
        files=group_files,
    )


def build_groups(mods_path: Path) -> list[GroupInfo]:
    files = scan_files(mods_path)
    file_index = index_files_by_stem(files)
    json_files = find_workshop_json_files(files)

    groups: list[GroupInfo] = []
    for index, json_path in enumerate(json_files, start=1):
        groups.append(make_group(index, json_path, file_index, mods_path))

    return groups


def group_to_dict(group: GroupInfo) -> dict:
    return {
        "id": group.id,
        "name": group.name,
        "json_path": group.json_path,
        "file_count": len(group.files),
    }


def group_from_dict(raw: dict) -> GroupInfo:
    return GroupInfo(
        id=int(raw["id"]),
        name=raw.get("name", ""),
        json_path=raw["json_path"],
    )


def load_groups() -> list[GroupInfo]:
    return [group_from_dict(g) for g in load_data().get("groups", [])]


def save_groups(groups: list[GroupInfo]) -> None:
    data = load_data()
    data["groups"] = [group_to_dict(g) for g in groups]
    save_data(data)


def carry_names(old_groups: list[GroupInfo], new_groups: list[GroupInfo]) -> None:
    old_name_by_json_path = {
        file_identity(old.json_path): old.name
        for old in old_groups
        if old.name.strip() and old.json_path.strip()
    }

    for new in new_groups:
        old_name = old_name_by_json_path.get(file_identity(new.json_path), "")
        if old_name.strip():
            new.name = old_name


def extract_game_mode_from_json(json_path: str) -> str:
    path = Path(json_path)

    try:
        with path.open("r", encoding="utf-8-sig") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return ""
        game_mode = raw.get("GameMode", "")
        if isinstance(game_mode, str) and game_mode.strip():
            return game_mode.strip()
    except Exception as exc:
        print(f"警告：读取 GameMode 失败，已跳过：{path} | {exc}")

    return ""


def auto_name_unnamed_groups(groups: list[GroupInfo]) -> None:
    for group in groups:
        if group.name.strip():
            continue
        game_mode = extract_game_mode_from_json(group.json_path)
        if game_mode:
            group.name = game_mode


def refresh_groups(
    mods_path: Path,
    old_groups: Optional[list[GroupInfo]] = None,
) -> list[GroupInfo]:
    groups = build_groups(mods_path)

    if old_groups:
        carry_names(old_groups, groups)

    auto_name_unnamed_groups(groups)

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


def display_groups(groups: list[GroupInfo], mods_path: Path) -> None:
    print("\n========== 当前分组 ==========")

    if not groups:
        print("没有找到文件，无法生成分组。")
    else:
        for group in groups:
            name = group.name.strip() or "（空白）"
            json_name = Path(group.json_path).name if group.json_path else "无"

            print(
                f"[{group.id}] 名称：{name} | "
                f"JSON：{json_name} | "
                f"文件数：{len(group.files)}"
            )

    print("==============================")
    print(f"文件路径：{mods_path}\n")


def select_preview_images(group: GroupInfo, max_count: int = PREVIEW_IMAGE_COUNT) -> list[Path]:
    image_files = [
        Path(file.path)
        for file in group.files
        if Path(file.path).suffix.lower() in IMAGE_EXTENSIONS
        and Path(file.path).is_file()
    ]

    if not image_files:
        return []

    cover_image = find_same_name_workshop_image(Path(group.json_path))
    selected: list[Path] = []
    selected_paths: set[str] = set()

    if cover_image is not None and cover_image.is_file():
        selected.append(cover_image)
        selected_paths.add(file_identity(cover_image))

    rest = [
        path for path in image_files
        if file_identity(path) not in selected_paths
    ]

    remaining_count = max(0, max_count - len(selected))
    selected.extend(random.sample(rest, min(remaining_count, len(rest))))

    return selected


def show_images_in_group(group: GroupInfo, image_files: list[Path]) -> None:
    selected = image_files[:PREVIEW_IMAGE_COUNT]

    if not selected:
        print("该分组中没有找到图片文件。")
        return

    if tk._default_root is None:
        root = tk.Tk()
        root.withdraw()

    window = tk.Toplevel()
    window.title(f"分组 {group.id} 图片预览")

    thumbs = []

    for index, image_path in enumerate(selected):
        try:
            img = Image.open(image_path)
            img.thumbnail((240, 240))

            photo = ImageTk.PhotoImage(img)
            thumbs.append(photo)

            label = tk.Label(window, image=photo)
            label.grid(row=index // 5, column=index % 5, padx=8, pady=8)

        except Exception as exc:
            print(f"图片读取失败：{image_path} | {exc}")

    window.thumbs = thumbs
    IMAGE_WINDOWS.append(window)

    window.update()


def display_files(groups: list[GroupInfo]) -> None:
    group_id = input_group_id(groups)
    if group_id is None:
        return

    group = find_group(groups, group_id)
    if not group:
        return

    preview_images = select_preview_images(group, PREVIEW_IMAGE_COUNT)
    show_images_in_group(group, preview_images)

    print("========== Workshop JSON ===========")
    for index, image_path in enumerate(preview_images, start=1):
        print(f"[{index}] {image_path}")

    print("========== 图片文件 ===========")
    count = 0
    for file in group.files:
        if Path(file.path).suffix.lower() in IMAGE_EXTENSIONS:
            count += 1
            if count > PREVIEW_IMAGE_COUNT:
                print("...")
                break
            print(f"[{count}] {file.path}")

    print("========== 其他文件 ===========")
    count = 0
    for file in group.files:
        suffix = Path(file.path).suffix.lower()
        if suffix in IMAGE_EXTENSIONS | {".obj", ".fbx", ".unity3d", ".rawt", ".rawm"}:
            continue
        count += 1
        if count > PREVIEW_IMAGE_COUNT:
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

    if not group.name:
        group.name = extract_game_mode_from_json(group.json_path)

    save_groups(groups)
    print("已保存分组名称。")


def delete_file(
    path: Path,
    move_to_backup: bool,
    backup_group_dir: Optional[Path],
) -> bool:
    try:
        if move_to_backup:
            if backup_group_dir is None:
                raise ValueError("backup_group_dir 不能为空")

            target = safe_backup_path(path, backup_group_dir)
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
    json_name = Path(group.json_path).name if group.json_path else "无"
    print(f"将删除分组 [{group.id}] 名称：{name}，JSON：{json_name}，文件数：{len(group.files)}")

    confirm = input("确认删除该分组内所有文件吗？(输入 Y 确认)：").strip()

    if confirm.upper() != "Y":
        print("已取消删除。")
        return groups

    deleted = failed = 0

    move_to_backup = get_config()["move_deleted_files_to_backup"]
    backup_group_dir = make_backup_group_dir(group) if move_to_backup else None

    # 收集其他分组中的文件路径，以避免误删被多个分组引用的文件
    other_group_file_paths = {
    file_identity(file)
    for other_group in groups
    if other_group.id != group.id
    for file in other_group.files
    }
    
    skipped_shared = 0
    for file in group.files:
        # 如果其他分组也引用了这个文件，就跳过删除
        if file_identity(file) in other_group_file_paths:
            skipped_shared += 1
            continue

        path = Path(file.path)
        if not path.is_file():
            continue

        if delete_file(path, move_to_backup, backup_group_dir):
            deleted += 1
        else:
            failed += 1

    print(f"删除完成。成功：{deleted}，失败：{failed}，跳过共享文件：{skipped_shared}")

    remaining_old_groups = [g for g in groups if g.id != group_id]
    return refresh_groups(get_mods_path(), remaining_old_groups)
# endregion


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
        display_groups(groups, mods_path)
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
