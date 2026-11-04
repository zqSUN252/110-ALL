"""One fail-closed private-storage ACL policy for API and runtime initialization."""
import os
from pathlib import Path
import stat
import subprocess


def protect_private_root(path: Path):
    path = Path(path).absolute()
    path.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        # New security descriptors discard ALL pre-existing explicit/inherited
        # grants. Only the executing service account is approved. No reparse-point
        # traversal and no removal/movement of data are allowed.
        script = r'''
$ErrorActionPreference='Stop'
$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User
$stack=New-Object 'System.Collections.Generic.Stack[string]'
$stack.Push($env:MCSTAR_PRIVATE_ACL_TARGET)
while($stack.Count -gt 0){
    $item=$stack.Pop()
    $attributes=[System.IO.File]::GetAttributes($item)
    if(($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0){throw 'Reparse point in private storage'}
    $directory=($attributes -band [System.IO.FileAttributes]::Directory) -ne 0
    if($directory){
        $acl=New-Object System.Security.AccessControl.DirectorySecurity
        $rule=New-Object System.Security.AccessControl.FileSystemAccessRule($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
    }else{
        $acl=New-Object System.Security.AccessControl.FileSecurity
        $rule=New-Object System.Security.AccessControl.FileSystemAccessRule($sid,'FullControl','Allow')
    }
    $acl.SetAccessRuleProtection($true,$false)
    $acl.SetOwner($sid)
    $acl.AddAccessRule($rule)
    if($directory){
        [System.IO.Directory]::SetAccessControl($item,$acl)
        $verified=[System.IO.Directory]::GetAccessControl($item)
    }else{
        [System.IO.File]::SetAccessControl($item,$acl)
        $verified=[System.IO.File]::GetAccessControl($item)
    }
    if(-not $verified.AreAccessRulesProtected -or $verified.Access.Count -ne 1){throw 'Private ACL validation failed'}
    $entry=$verified.Access[0]
    if($entry.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value -ne $sid.Value -or
       $entry.AccessControlType -ne 'Allow' -or $entry.FileSystemRights -ne 'FullControl'){
        throw 'Private ACL principal validation failed'
    }
    if($directory){foreach($child in [System.IO.Directory]::GetFileSystemEntries($item)){$stack.Push($child)}}
}
'''
        result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
            env={**os.environ, 'MCSTAR_PRIVATE_ACL_TARGET': str(path)}, capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError('Cannot enforce approved-principal ACL on private storage; startup refused')
    else:
        pending = [path]
        while pending:
            item = pending.pop()
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid != os.geteuid():
                raise RuntimeError('Private storage contains symlink or foreign owner; startup refused')
            directory = stat.S_ISDIR(info.st_mode)
            item.chmod(0o700 if directory else 0o600)
            if directory:
                pending.extend(item.iterdir())
