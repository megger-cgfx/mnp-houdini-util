"""
Houdini Cleanup Utility for Managing Disk Space
Operated from MNP Filecache or shelf UI, marking folders with explicit files
"""

import os
import sys
import subprocess
import shutil
from typing import List, Tuple

import hou


class HoudiniCacheCleanup:
    """Utility class for managing and cleaning up Houdini cache files."""

    def __init__(self):
        self.root_dir = hou.getenv("ROOT")
        self.geo_dir = f"{hou.expandString('$HIP')}/geo"
        
        # Add pipeline sitepackages to path
        pipeline_packages = f"{self.root_dir}/00_pipeline/01_houdini/python2.7libs/site-packages"
        sys.path.append(pipeline_packages)
        
        # Import after path addition
        try:
            import scandir
            self.scandir = scandir
        except ImportError:
            raise ImportError("Required package 'scandir' not found in pipeline packages")

        # Marker file names
        self.ACTIVE_MARKER = "/__cleanup_active_marker.txt"
        self.DELETE_MARKER = "/__cleanup_delete_marker.txt"
        self.FILE_NODES = ["mnp_filecache"]

    def _create_marker_file(self, directory: str, marker_name: str) -> None:
        """Create a marker file in the specified directory."""
        with open(directory + marker_name, "w+") as _:
            pass

    def _remove_marker_file(self, directory: str, marker_name: str) -> None:
        """Remove a marker file if it exists."""
        marker_path = directory + marker_name
        if os.path.exists(marker_path):
            os.remove(marker_path)

    def _get_all_cache_directories(self) -> List[str]:
        """Get all cache directories under the geo directory."""
        cache_dirs = []
        for root, folders, _ in self.scandir.walk(self.geo_dir):
            for folder in folders:
                folder_dir = os.path.abspath(os.path.join(root, folder))
                cache_dirs.append(folder_dir)
        return cache_dirs

    def mark_active_caches(self) -> Tuple[int, int, int, int]:
        """
        Mark active cache directories and prepare others for deletion.
        
        Returns:
            Tuple containing counts of (active, protected, total, to_delete) directories
        """
        counters = {
            'active': 0,
            'protected': 0,
            'total': 0,
            'delete': 0
        }

        print("\n----------")
        print("\nFolders active in current HIP:")
        
        # Mark active directories
        for node in hou.node("/").allSubChildren():
            if node.type().nameComponents()[2] in self.FILE_NODES:
                filepath = node.parm("outputfile").eval()
                cache_dir = os.path.abspath(os.path.split(filepath)[0])
                
                if os.path.exists(cache_dir):
                    self._create_marker_file(cache_dir, self.ACTIVE_MARKER)
                    self._remove_marker_file(cache_dir, self.DELETE_MARKER)
                    print(f"  {cache_dir} (ACTIVE MARKER SET)")
                    counters['active'] += 1

        # Process all directories
        print("\nAll Folders in '/geo':")
        cache_dirs = self._get_all_cache_directories()
        
        for cache_dir in cache_dirs:
            counters['total'] += 1
            if os.path.exists(cache_dir + self.ACTIVE_MARKER):
                print(f"  {cache_dir} (PROTECTED)")
                counters['protected'] += 1
                continue
            print(f"  {cache_dir}")

        # Mark directories for deletion
        print("\nFolders to delete:")
        for cache_dir in cache_dirs:
            if not (os.path.exists(cache_dir + self.ACTIVE_MARKER) or 
                   "__rescued" in cache_dir):
                self._create_marker_file(cache_dir, self.DELETE_MARKER)
                counters['delete'] += 1
                print(f"  {cache_dir} (DELETE MARKER SET)")

        # Print summary
        print(f"\n\nCount Active:     {counters['active']}")
        print(f"Count Protected:  {counters['protected']}")
        print(f"Count Total:      {counters['total']}")
        print(f"Count Delete:     {counters['delete']}")

        return (counters['active'], counters['protected'], 
                counters['total'], counters['delete'])

    def clear_markers(self) -> None:
        """Remove all cleanup markers from the geo directory."""
        print("\n----------")
        print("\nMarkers removed from Folders in '/geo':")
        
        for cache_dir in self._get_all_cache_directories():
            if os.path.exists(cache_dir + self.DELETE_MARKER):
                self._remove_marker_file(cache_dir, self.DELETE_MARKER)
                print(f"   deletemarker: {cache_dir}")
            if os.path.exists(cache_dir + self.ACTIVE_MARKER):
                self._remove_marker_file(cache_dir, self.ACTIVE_MARKER)
                print(f"-> ACTIVEMARKER: {cache_dir}")

    def execute_cleanup(self) -> None:
        """Execute the cleanup based on delete markers."""
        print("\n----------")
        
        # Collect directories marked for deletion
        folders_to_delete = []
        total_size = 0
        file_count = 0
        
        for root, folders, _ in self.scandir.walk(self.geo_dir):
            for folder in folders:
                folder_dir = os.path.abspath(os.path.join(root, folder))
                if os.path.exists(folder_dir + self.DELETE_MARKER):
                    folders_to_delete.append(folder_dir)
                    
                    # Calculate folder size and file count
                    for path, _, files in os.walk(folder_dir):
                        for file in files:
                            file_count += 1
                            total_size += os.path.getsize(os.path.join(path, file))

        if not folders_to_delete:
            print("\nNo Folders to be deleted.\n")
            return

        # Convert size to GB and format message
        size_gb = str(round(total_size / (1024 * 1024 * 1024 * 100)) / 100)
        message = [
            f"     {len(folders_to_delete)} Folders to be deleted.",
            f"     ({file_count} Files, {size_gb} GB)",
            "     See console for list.\n",
            "Press OK to proceed with deletion."
        ]

        # Display confirmation dialog
        print("\n----------------------------------------------------")
        print(f"{len(folders_to_delete)} Folders to be deleted.\n")
        print("List: ")
        print("\n".join(folders_to_delete))
        print(f"\nFile Count: {file_count}")
        print(f"Total Size: {size_gb} GB")

        should_delete = hou.ui.displayConfirmation(
            '\n'.join(message),
            title="File Cleanup Confirmation",
            severity=hou.severityType.ImportantMessage,
            suppress=hou.confirmType.SiblingChannelGroups
        )

        if not should_delete:
            print("\nDeletion aborted.\n")
            return

        # Backup .hip files
        backup_dir = os.path.join(self.geo_dir, "__rescued_hip_backups")
        for folder_dir in folders_to_delete:
            for path, _, files in os.walk(folder_dir):
                for file in files:
                    if ".hip" in file:
                        source_path = os.path.join(path, file)
                        target_name = f"{os.path.split(path)[1]}__{file}"
                        target_path = os.path.join(backup_dir, target_name)
                        os.makedirs(backup_dir, exist_ok=True)
                        shutil.copyfile(source_path, target_path)

        # Create cleanup batch file
        bat_commands = ["@echo off"]
        for folder in folders_to_delete:
            bat_commands.append(
                f'if exist "{folder}" rmdir "{folder}" /q /s & echo "removed {folder}"'
            )
        bat_commands.extend(["echo Finished Deletion with .bat", "pause"])

        # Save and execute batch file
        hip_name = os.path.splitext(os.path.basename(hou.hipFile.path()))[0]
        bat_dir = os.path.join(self.geo_dir, "__cleanup_bats")
        bat_path = os.path.join(bat_dir, f"{hip_name}_cleanup_geocaches.bat")
        
        os.makedirs(bat_dir, exist_ok=True)
        with open(bat_path, "w") as bat_file:
            bat_file.write("\n".join(bat_commands))

        print(f"\nStarting Deletion with external .bat:\n{bat_path}\n")
        subprocess.Popen(f"start {os.path.abspath(bat_path)}", shell=True)


# Usage example:
def main():
    cleanup = HoudiniCacheCleanup()
    # Following methods for testing:
    # cleanup.mark_active_caches()
    # cleanup.clear_markers()
    # cleanup.execute_cleanup()

if __name__ == "__main__":
    main()