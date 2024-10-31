"""
Houdini Utilities for File Management and Parameter Wedging
Provides functionality for managing hip files, output scanning, and parameter wedging operations.
"""

import os
import re
import math
import shutil
import hou
from typing import Dict, List, Tuple, Optional, Union


class FileManager:
    """Handles file operations and directory scanning for Houdini outputs."""
    
    @staticmethod
    def backup_hip(output_file: str, rename: bool = True) -> str:
        """
        Copy hip file to output directory when cache is written.
        
        Args:
            output_file: Path to output file
            rename: Whether to rename the backup file
            
        Returns:
            Path to backed up file
            
        Raises:
            OSError: If directory operations fail
        """
        directory = os.path.dirname(output_file)
        hip_name = hou.hipFile.basename()

        # Ensure directory exists
        if not os.path.exists(directory):
            os.makedirs(directory)

        # Save backup
        try:
            hou.putenv("HOUDINI_BACKUP_DIR", directory)
            hou.hipFile.saveAsBackup()
        finally:  # Ensure we always unset the env var
            hou.unsetenv("HOUDINI_BACKUP_DIR")

        # Get newest backup
        try:
            backup_files = [
                os.path.join(directory, f) for f in os.listdir(directory)
                if ".hip" in f and "_bak" in f and "_submitted" not in f
            ]
            
            if not backup_files:
                raise RuntimeError("No backup files found after save")
                
            newest_backup = sorted(backup_files)[-1]
            
            if rename:
                new_name = os.path.join(directory, hip_name)
                shutil.move(newest_backup, new_name)
                return new_name
            return newest_backup
        except Exception as e:
            raise RuntimeError(f"Failed to process backup file: {str(e)}")

    @staticmethod
    def scan_output_directory(directory: str, return_mode: int) -> Optional[str]:
        """
        Scan output directory and generate info about output state.
        
        Args:
            directory: Directory to scan or "outputfile" to use node parameter
            return_mode: Whether to return message (1) or set as node comment (0)
            
        Returns:
            Status message if return_mode is 1, None otherwise
        """
        node = hou.pwd()
        
        if directory == "outputfile":
            try:
                directory = os.path.dirname(node.parm('outputfile').eval())
            except Exception as e:
                return f"Error getting output file: {str(e)}"

        try:
            enable_scan = node.parm('enable_scan_outdir').eval()
        except:
            enable_scan = True

        if not enable_scan:
            message = ""
        elif not os.path.exists(directory):
            message = "Not yet cached."
        else:
            try:
                stats = FileManager._calculate_directory_stats(directory)
                message = FileManager._format_directory_stats(stats)
            except Exception as e:
                message = f"Error scanning directory: {str(e)}"

        if return_mode == 1:
            return message
        node.setComment(message)
        return None

    @staticmethod
    def _calculate_directory_stats(directory: str) -> Dict[str, Union[int, List[float]]]:
        """
        Calculate statistics for files in directory.
        
        Args:
            directory: Directory to analyze
            
        Returns:
            Dictionary containing count, durations, and size statistics
        """
        counter = 0
        duration_list = [0]
        folder_size = 0
        prev_file = ""

        for path, _, files in os.walk(directory):
            for file in sorted(files):  # Sort for consistent processing
                if ".hip" in file or "marker.txt" in file:
                    continue

                filename = os.path.join(path, file)
                folder_size += os.path.getsize(filename)
                mod_time = os.path.getmtime(filename)

                if counter > 0:
                    prev_time = os.path.getmtime(prev_file)
                    duration = mod_time - prev_time
                    if duration > 0:
                        duration_list.append(duration)
                
                prev_file = filename
                counter += 1

        return {
            'count': counter,
            'durations': duration_list,
            'size': folder_size
        }

    @staticmethod
    def _format_directory_stats(stats: Dict[str, Union[int, List[float]]]) -> str:
        """
        Format directory statistics into readable message.
        
        Args:
            stats: Dictionary containing directory statistics
            
        Returns:
            Formatted message string
        """
        if stats['count'] < 1:
            return "Not yet cached.\nFolder exists."

        def format_duration(seconds: float) -> str:
            """Format duration in seconds to human-readable string."""
            if seconds > 3600:
                return f"{round(seconds/3600, 1)}h"
            elif seconds > 60:
                return f"{int(round(seconds/60, 0))}m"
            return f"{int(round(seconds, 0))}s"

        durations: List[float] = stats['durations']
        avg_duration = sum(durations) / len(durations)
        max_duration = max(durations)
        total_duration = sum(durations)
        size_gb = round(stats['size'] / (1024 * 1024.0 * 10)) / 100
        
        return (
            f"Cached files: {stats['count']}\n"
            f"Cache time: {format_duration(total_duration)} "
            f"({format_duration(avg_duration)} avg, "
            f"{format_duration(max_duration)} max)\n"
            f"Cached size: {size_gb:.2f} GB"
        )


class NodeManager:
    """Handles Houdini node operations."""
    
    @staticmethod
    def make_read_node() -> Optional[hou.Node]:
        """
        Create a file node for reading output.
        
        Returns:
            Created node or None if creation fails
        """
        try:
            node = hou.pwd()
            output = node.parm("outputfile").eval()

            # Create node
            read_node = node.parent().createNode("file")
            read_node.setPosition(node.position())
            read_node.move([1, -2])
            read_node.setColor(node.color())
            read_node.setUserData("nodeshape", "tabbed_right")

            # Handle frame numbers
            if node.parm("trange").eval() > 0:
                frame_number = re.findall(r"\d{4}", output)[-1]
                output = output.replace(frame_number, "`$F4`")

            # Set name with version and wedge info
            version = f"V{node.parm('version').eval()}"
            wedge = (f"W{node.parm('wedgenum').eval()}" 
                    if node.parm("enable_wedging").eval() else "")
            read_node.setName(f"{node.name()}_{version}{wedge}_read", True)

            read_node.parm("file").set(output)
            return read_node
            
        except Exception as e:
            raise RuntimeError(f"Failed to create read node: {str(e)}")


class WedgeManager:
    """Manages parameter wedging operations."""
    
    def __init__(self, node: hou.Node):
        """
        Initialize WedgeManager.
        
        Args:
            node: Houdini node to manage wedging for
        """
        self.node = node
        
    def clean_references(self) -> None:
        """Remove references and push chosen values to wedged parameters."""
        if not self.node.parm("w_autoset_references").eval():
            return
            
        num_params = self.node.parm("w_parameters").eval()
        for i in range(num_params):
            param = self.node.parm(f"w_targetparm{i+1}")
            if not param:
                continue
                
            value = param.rawValue()
            new_value = value.lstrip("`chs('/\"").rstrip("\")`")
            
            param.revertToDefaults()
            prefix = "" if new_value.startswith("../") else "/"
            param.set(prefix + new_value)

    def auto_fetch_ranges(self) -> None:
        """Estimate parameter ranges based on interface metadata."""
        num_params = self.node.parm("w_parameters").eval()
        messages = ["Parameter wedge ranges inferred from defaults:"]

        for i in range(num_params):
            source = self.node.parm(f"w_value{i+1}")
            target_path = self.node.parm(f"w_targetparm{i+1}").eval()
            target = hou.parm(target_path)

            if target is None:
                continue

            messages.append(f"     - {target.name()}")
            
            template = target.parmTemplate()
            min_val = float(template.minValue())
            max_val = float(template.maxValue())
            default = float(template.defaultValue()[0])
            range_val = max_val - min_val

            try:
                if default <= min_val or default >= max_val:
                    exp = 1
                else:
                    exp = math.log10((default-min_val)/range_val) / math.log10(0.5)
            except (ValueError, ZeroDivisionError):
                exp = 1

            self.node.parm(f"w_min{i+1}").set(min_val)
            self.node.parm(f"w_max{i+1}").set(max_val)
            self.node.parm(f"w_pow{i+1}").set(exp)

        hou.ui.displayMessage("\n".join(messages))

    def set_parameters(self) -> None:
        """Set local parameter expressions for wedge iteration control."""
        num_params = self.node.parm("w_parameters").eval()
        method = self.node.parm("w_method").evalAsString()
        enable_power = self.node.parm("enable_power").eval()

        for i in range(num_params):
            expr = self._generate_wedge_expression(i, method, enable_power)
            param = self.node.parm(f"w_value{i+1}")
            if param:
                param.setExpression(expr)
            
            rand_expr = self._generate_rand_expression(i, method)
            if rand_expr:
                rand_param = self.node.parm(f"w_value_rand{i+1}")
                if rand_param:
                    rand_param.setExpression(rand_expr)

            if self.node.parm("w_toggle_ui").eval():
                self._update_parameter_name(i)

    def _generate_wedge_expression(self, index: int, method: str, enable_power: bool) -> str:
        """
        Generate the appropriate wedge expression based on method.
        
        Args:
            index: Parameter index
            method: Wedge method ('r', 's', or 'se')
            enable_power: Whether to enable power adjustment
            
        Returns:
            Generated expression string
        """
        i = index + 1
        
        if method == 'r':
            base = f'fit01(rand(ch("wedgenum")+1634*ch("w_seed")+{i})'
        elif method == 's':
            base = 'fit01(fit(ch("wedgenum"),0,ch("wedge_iterations")-1,0,1)'
        else:  # 'se'
            return self._generate_sequential_expression(index, i)

        if enable_power:
            base += f',pow(ch("w_pow{i}"))'
            
        return f'{base},ch("w_min{i}"),ch("w_max{i}"))'

    def _generate_sequential_expression(self, index: int, i: int) -> str:
        """
        Generate expression for sequential wedging.
        
        Args:
            index: Parameter index
            i: One-based parameter index
            
        Returns:
            Generated expression string
        """
        iters = self.node.parm("wedge_iterations").eval()
        p_min = index * iters
        p_max = ((index + 1) * iters) - 1
        
        return (
            f'if(ch("wedgenum")<{p_min}||ch("wedgenum")>{p_max},'
            f'ch("w_value_orig{i}"),'
            f'fit01(fit(ch("wedgenum"),{p_min},{p_max},0,1),'
            f'ch("w_min{i}"),ch("w_max{i}"))'
            ')'
        )

    def _generate_rand_expression(self, index: int, method: str) -> Optional[str]:
        """
        Generate random expression for parameter visualization.
        
        Args:
            index: Parameter index
            method: Wedge method
            
        Returns:
            Generated expression string or None
        """
        iters = self.node.parm("wedge_iterations").eval()
        if method == 'r':
            return f'rand(ch("wedgenum")+1634*ch("w_seed")+{index+1})'
        elif method == 'se':
            p_min = index * iters
            p_max = ((index + 1) * iters) - 1
            return (
                f'if(ch("wedgenum")<{p_min}||ch("wedgenum")>{p_max},'
                f'ch("w_se_default"),'
                f'fit(ch("wedgenum"),{p_min},{p_max},0,1))'
            )
        return None

    def _update_parameter_name(self, index: int) -> None:
        """
        Update the display name for a wedge parameter.
        
        Args:
            index: Parameter index
        """
        param = self.node.parm(f"w_targetparm{index+1}")
        if not param:
            return
            
        path_parts = param.eval().split("/")
        if len(path_parts) >= 2:
            name = f"{path_parts[-2]} -- {path_parts[-1]}"
            name_param = self.node.parm(f"w_targetparm_name{index+1}")
            if name_param:
                name_param.set(name)


def set_wedge_defaults(which: str = 'wedge') -> None:
    """
    Set default output paths for wedging operations.
    
    Args:
        which: Type of defaults to set ('default' or 'wedge')
    """
    base_path = '$HIP/geo/`chs("name")`.v`chs("versionp")`'
    base_daily = '$HIP/preview/`chs("name")`.v`chs("versionp")`'
    
    if which == 'default':
        path = f'{base_path}/`chs("name")`.v`chs("versionp")`.`chs("mnp_cacher/f4")`.`chs("type")`'
        daily = f'{base_daily}/`chs("name")`.v`chs("versionp")`.$F4.jpg'
    else:
        path = f'{base_path}/w_`chs("wedgenum")`.0/`chs("name")`.v`chs("versionp")