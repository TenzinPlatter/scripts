#!/usr/bin/env python3
import os
import sys
import time
import subprocess
import threading
import fnmatch
import yaml
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Ensure stdout/stderr are unbuffered for journalctl
sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 1)
sys.stderr = os.fdopen(sys.stderr.fileno(), 'w', 1)

def load_config():
    """Load configuration from YAML file with fallback to defaults"""
    config_path = Path(__file__).parent / "config.yaml"
    
    # Default configuration
    default_config = {
        'watch_directory': '~/.dotfiles',
        'repo_directory': '~/.dotfiles',
        'commit_delay': 60,
        'fetch_interval': 600,
        'enable_notifications': True,
        'notify_on_commit': True,
        'notify_on_remote_changes': True,
        'auto_push': True,
        'respect_gitignore': True,
        'excluded_patterns': [
            '.git/', '.git\\', '/.git/', '\\.git\\', 'index.lock', 'COMMIT_EDITMSG',
            'HEAD.lock', 'refs/heads/', 'refs/remotes/', 'logs/HEAD', 'logs/refs/',
            'objects/', 'hooks/', 'info/', 'packed-refs', 'config.lock', 'shallow.lock',
            'modules/', '.git/index', '.git/HEAD', '.git/refs/', '.git/logs/',
            '.git/objects/', '.git/modules/', 'ORIG_HEAD', 'FETCH_HEAD', 'MERGE_HEAD',
            'CHERRY_PICK_HEAD', '.tmp_', '__pycache__/', '.pyc', '.pyo', '.swp',
            '.swo', '.tmp', '~', '.bak'
        ]
    }
    
    try:
        if config_path.exists():
            with open(config_path, 'r') as f:
                user_config = yaml.safe_load(f) or {}
            # Merge user config with defaults
            config = {**default_config, **user_config}
            print(f"📄 Loaded configuration from {config_path}")
        else:
            config = default_config
            print(f"⚠️  Config file not found at {config_path}, using defaults")
    except Exception as e:
        print(f"❌ Error loading config file: {e}")
        print("   Using default configuration")
        config = default_config
    
    # Expand user paths
    config['watch_directory'] = os.path.expanduser(config['watch_directory'])
    config['repo_directory'] = os.path.expanduser(config['repo_directory'])
    
    return config

# Load configuration
CONFIG = load_config()

class GitCommitHandler(FileSystemEventHandler):
    def __init__(self, config):
        self.config = config
        self.watch_dir = Path(config['watch_directory']).resolve()
        self.repo_dir = Path(config['repo_directory']).resolve()
        self.submodules = self._get_submodules()
        self.pending_commits = {}  # Track pending commits per directory
        self.commit_timers = {}  # Track active timers per directory
        self.timer_lock = threading.Lock()  # Thread safety for timer operations
        self.fetch_timer = None  # Timer for periodic fetching
        self.excluded_patterns = config['excluded_patterns']
        self.gitignore_patterns = {}  # Cache gitignore patterns per directory
        self.recent_temp_files = {}  # Track files that might be temporary
        
        # Load gitignore patterns if enabled
        if self.config['respect_gitignore']:
            self._load_gitignore_patterns()
        
        self._commit_existing_changes()
        self.start_fetch_timer()
        
    def _run_git_command(self, cmd, cwd, description="Git command"):
        """Run a git command with proper error logging"""
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True
            )
            
            # Log command details if it fails
            if result.returncode != 0:
                cmd_str = ' '.join(cmd)
                print(f"❌ {description} failed: {cmd_str}")
                print(f"   Working directory: {cwd}")
                print(f"   Return code: {result.returncode}")
                if result.stdout.strip():
                    print(f"   STDOUT: {result.stdout.strip()}")
                if result.stderr.strip():
                    print(f"   STDERR: {result.stderr.strip()}")
            
            return result
            
        except subprocess.CalledProcessError as e:
            cmd_str = ' '.join(cmd)
            print(f"❌ {description} subprocess error: {cmd_str}")
            print(f"   Working directory: {cwd}")
            print(f"   Exception: {e}")
            raise
        except Exception as e:
            cmd_str = ' '.join(cmd)
            print(f"❌ {description} unexpected error: {cmd_str}")
            print(f"   Working directory: {cwd}")
            print(f"   Exception: {e}")
            raise
        
    def _get_submodules(self):
        """Get list of submodule paths"""
        try:
            result = self._run_git_command(
                ['git', 'submodule', 'foreach', '--quiet', 'echo $sm_path'],
                self.repo_dir,
                "Get submodules"
            )
            if result.returncode == 0:
                return [Path(self.repo_dir / line.strip()) for line in result.stdout.strip().split('\n') if line.strip()]
            else:
                return []
        except Exception:
            return []
    
    def _is_in_submodule(self, file_path):
        """Check if file is in a submodule"""
        file_path = Path(file_path).resolve()
        return any(file_path.is_relative_to(submodule) for submodule in self.submodules)
    
    def _get_submodule_for_file(self, file_path):
        """Get the submodule directory for a file"""
        file_path = Path(file_path).resolve()
        for submodule in self.submodules:
            if file_path.is_relative_to(submodule):
                return submodule
        return None
    
    def _should_exclude_file(self, file_path):
        """Check if file should be excluded from git operations"""
        file_path = Path(file_path).resolve()
        file_path_str = str(file_path)
        file_name = file_path.name
        
        # Check if any excluded pattern matches the file path
        for pattern in self.excluded_patterns:
            if pattern in file_path_str:
                return True
        
        # Check if filename ends with excluded extensions
        if file_name.endswith(('.lock', '.tmp', '.swp', '.swo', '~', '.bak')):
            return True
        
        # Check for numeric-only filenames (common for temp files)
        if file_name.isdigit():
            return True
        
        # Check for vim/editor temp file patterns
        if (file_name.startswith('.') and file_name.endswith('.swp')) or \
           (file_name.startswith('.') and file_name.endswith('.swo')) or \
           file_name.startswith('4913') or \
           (len(file_name) == 4 and file_name.isdigit()) or \
           (len(file_name) <= 6 and file_name.isdigit()):
            return True
        
        # Check for other suspicious patterns
        if (file_name.startswith('.#') or  # Emacs lock files
            file_name.endswith('#') or     # Emacs backup files
            file_name.startswith('#') or   # Various temp files
            file_name.endswith('.orig') or # Git merge files
            file_name.endswith('.rej')):   # Patch reject files
            return True
        
        # Check if it's a git internal file (more comprehensive)
        if ('/.git/' in file_path_str or 
            '\\.git\\' in file_path_str or
            file_path_str.endswith('/.git') or
            '/.git/index' in file_path_str or
            any(part.startswith('.git') for part in file_path.parts)):
            return True
        
        # Check for git metadata files in any directory
        git_files = {'COMMIT_EDITMSG', 'ORIG_HEAD', 'FETCH_HEAD', 'MERGE_HEAD', 'CHERRY_PICK_HEAD', 'HEAD', 'index'}
        if file_name in git_files:
            return True
        
        # Check if file matches gitignore patterns
        if self._matches_gitignore(file_path):
            return True
        
        return False
    
    def _load_gitignore_patterns(self):
        """Load gitignore patterns from all .gitignore files"""
        # Load main repo gitignore
        self._load_gitignore_for_repo(self.repo_dir)
        
        # Load submodule gitignores
        for submodule in self.submodules:
            self._load_gitignore_for_repo(submodule)
    
    def _load_gitignore_for_repo(self, repo_dir):
        """Load gitignore patterns for a specific repository"""
        gitignore_file = repo_dir / '.gitignore'
        if gitignore_file.exists():
            try:
                with open(gitignore_file, 'r', encoding='utf-8') as f:
                    patterns = []
                    for line in f:
                        line = line.strip()
                        # Skip empty lines and comments
                        if line and not line.startswith('#'):
                            patterns.append(line)
                    self.gitignore_patterns[str(repo_dir)] = patterns
            except Exception as e:
                print(f"⚠️  Error reading .gitignore in {repo_dir}: {e}")
    
    def _matches_gitignore(self, file_path):
        """Check if file matches any gitignore pattern"""
        file_path = Path(file_path).resolve()
        
        # Determine which repository this file belongs to
        repo_dir = None
        if self._is_in_submodule(file_path):
            repo_dir = self._get_submodule_for_file(file_path)
        else:
            repo_dir = self.repo_dir
        
        if not repo_dir or str(repo_dir) not in self.gitignore_patterns:
            return False
        
        patterns = self.gitignore_patterns[str(repo_dir)]
        
        # Get relative path from repo root
        try:
            rel_path = file_path.relative_to(repo_dir)
            rel_path_str = str(rel_path)
            
            # Check each gitignore pattern
            for pattern in patterns:
                # Handle negation patterns (starting with !)
                if pattern.startswith('!'):
                    continue  # Skip negation patterns for now (complex logic)
                
                # Handle directory patterns (ending with /)
                if pattern.endswith('/'):
                    if rel_path.is_dir() and fnmatch.fnmatch(rel_path_str, pattern[:-1]):
                        return True
                    # Check if file is inside a directory that matches
                    for parent in rel_path.parents:
                        if fnmatch.fnmatch(str(parent), pattern[:-1]):
                            return True
                else:
                    # File pattern matching
                    if fnmatch.fnmatch(rel_path_str, pattern):
                        return True
                    # Check filename only
                    if fnmatch.fnmatch(file_path.name, pattern):
                        return True
                    # Check if any parent directory matches
                    for parent in rel_path.parents:
                        if fnmatch.fnmatch(str(parent), pattern):
                            return True
            
            return False
            
        except ValueError:
            # File is not relative to repo dir
            return False
    
    def get_relative_directory(self, file_path):
        """Get the directory containing the changed file, relative to watch directory"""
        file_path = Path(file_path).resolve()
        
        # Get the directory containing the file
        if file_path.is_file():
            containing_dir = file_path.parent
        else:
            containing_dir = file_path
            
        # Make it relative to the watch directory
        try:
            relative_dir = containing_dir.relative_to(self.watch_dir)
            if str(relative_dir) == '.':
                return str(self.watch_dir)
            else:
                return str(self.watch_dir / relative_dir)
        except ValueError:
            # File is outside watch directory
            return str(containing_dir)
    
    def schedule_commit(self, file_path, event_type):
        """Schedule a commit with delay, canceling any existing timer for the directory"""
        try:
            # Determine the directory that should be committed
            if self._is_in_submodule(file_path):
                target_dir = self._get_submodule_for_file(file_path)
                commit_type = "submodule"
            else:
                target_dir = self.repo_dir
                commit_type = "main"
            
            if not target_dir:
                return
                
            dir_key = str(target_dir)
            
            with self.timer_lock:
                # Cancel existing timer for this directory
                if dir_key in self.commit_timers:
                    self.commit_timers[dir_key].cancel()
                    print(f"⏰ Canceled previous timer for {target_dir.name}")
                
                # Store or update commit information
                if dir_key not in self.pending_commits:
                    self.pending_commits[dir_key] = {
                        'target_dir': target_dir,
                        'commit_type': commit_type,
                        'changes': []
                    }
                
                # Add this change to the list of changes
                self.pending_commits[dir_key]['changes'].append({
                    'file_path': file_path,
                    'event_type': event_type,
                    'file_name': Path(file_path).name
                })
                
                # Create new timer
                timer = threading.Timer(COMMIT_DELAY, self._execute_delayed_commit, [dir_key])
                self.commit_timers[dir_key] = timer
                timer.start()
                
                change_count = len(self.pending_commits[dir_key]['changes'])
                print(f"⏰ Scheduled commit for {target_dir.name} in {COMMIT_DELAY} seconds ({change_count} changes)")
                    
        except Exception as e:
            print(f"Error scheduling commit: {e}")
    
    def _execute_delayed_commit(self, dir_key):
        """Execute the delayed commit for a directory"""
        with self.timer_lock:
            if dir_key not in self.pending_commits:
                return
                
            commit_info = self.pending_commits[dir_key]
            changes = commit_info['changes']
            
            if not changes:
                return
            
            try:
                if commit_info['commit_type'] == 'submodule':
                    # First commit in the submodule
                    self._commit_squashed_submodule(commit_info['target_dir'], changes)
                    # Then commit the submodule change in the main repo
                    self._commit_submodule_update(commit_info['target_dir'])
                else:
                    # Regular files in main repo
                    self._commit_squashed_main_repo(changes)
                        
            except subprocess.CalledProcessError as e:
                print(f"Error running git command: {e}")
            except Exception as e:
                print(f"Unexpected error during commit: {e}")
            finally:
                # Clean up
                self.pending_commits.pop(dir_key, None)
                self.commit_timers.pop(dir_key, None)
    
    def _commit_squashed_submodule(self, submodule_dir, changes):
        """Commit multiple changes in a submodule as a single commit"""
        try:
            # Get commit message BEFORE adding files
            commit_message = self._create_squashed_commit_message(changes, submodule_dir.name)
            
            # Run git add in the submodule
            add_result = self._run_git_command(['git', 'add', '.'], submodule_dir, "Git add (submodule)")
            if add_result.returncode != 0:
                return
            
            # Run git commit in the submodule
            commit_result = self._run_git_command(
                ['git', 'commit', '-m', commit_message], 
                submodule_dir, 
                "Git commit (submodule)"
            )
            
            if commit_result.returncode == 0:
                print(f"✓ Submodule commit: {commit_message}")
                # Push the submodule changes
                self._push_changes(submodule_dir, f"submodule {submodule_dir.name}")
            else:
                if "nothing to commit" in commit_result.stdout:
                    print(f"No changes to commit in submodule {submodule_dir.name}")
                    
        except Exception as e:
            print(f"❌ Error during submodule commit: {e}")
    
    def _commit_squashed_main_repo(self, changes):
        """Commit multiple changes in main repo as a single commit"""
        try:
            # Get commit message BEFORE adding files
            commit_message = self._create_squashed_commit_message(changes, "main repo")
            
            # Run git add in the main repo
            add_result = self._run_git_command(['git', 'add', '.'], self.repo_dir, "Git add (main repo)")
            if add_result.returncode != 0:
                return
            
            # Run git commit in the main repo
            commit_result = self._run_git_command(
                ['git', 'commit', '-m', commit_message], 
                self.repo_dir, 
                "Git commit (main repo)"
            )
            
            if commit_result.returncode == 0:
                print(f"✓ Committed: {commit_message}")
                # Send notification for main repo commit
                self._send_commit_notification(commit_message)
                # Push the main repo changes
                self._push_changes(self.repo_dir, "main repo")
            else:
                if "nothing to commit" in commit_result.stdout:
                    print(f"No changes to commit in main repo")
                    
        except Exception as e:
            print(f"❌ Error during main repo commit: {e}")
    
    def _create_squashed_commit_message(self, changes, location):
        """Create a commit message that summarizes all changes from file events"""
        # Use the actual file paths from changes to determine git status for each file
        repo_dir = self.repo_dir if location == "main repo" else self._get_repo_for_location(location)
        
        try:
            # Get the unique files that actually changed from our events
            changed_files = set()
            for change in changes:
                file_path = Path(change['file_path'])
                changed_files.add(file_path)
            
            if not changed_files:
                return f"update files in {location}"
            
            # Check git status for each file that actually changed
            git_changes = {'added': [], 'modified': [], 'deleted': [], 'renamed': []}
            
            for file_path in changed_files:
                try:
                    # Check if file is tracked by git
                    tracked_result = self._run_git_command(
                        ['git', 'ls-files', '--error-unmatch', str(file_path.relative_to(repo_dir))],
                        repo_dir,
                        f"Check if file is tracked"
                    )
                    
                    # If file exists and is tracked, it's modified
                    if file_path.exists() and tracked_result.returncode == 0:
                        git_changes['modified'].append(file_path.name)
                    # If file doesn't exist but was tracked, it's deleted
                    elif not file_path.exists() and tracked_result.returncode == 0:
                        git_changes['deleted'].append(file_path.name)
                    # If file exists but not tracked, it's new
                    elif file_path.exists() and tracked_result.returncode != 0:
                        git_changes['added'].append(file_path.name)
                        
                except Exception:
                    # If we can't determine, assume it's modified
                    if file_path.exists():
                        git_changes['modified'].append(file_path.name)
                    else:
                        git_changes['deleted'].append(file_path.name)
            
            # Build commit message from our analysis
            message_parts = []
            
            if git_changes['added']:
                if len(git_changes['added']) == 1:
                    message_parts.append(f"add {git_changes['added'][0]}")
                else:
                    message_parts.append(f"add {len(git_changes['added'])} files")
            
            if git_changes['modified']:
                if len(git_changes['modified']) == 1:
                    message_parts.append(f"update {git_changes['modified'][0]}")
                else:
                    message_parts.append(f"update {len(git_changes['modified'])} files")
            
            if git_changes['deleted']:
                if len(git_changes['deleted']) == 1:
                    message_parts.append(f"remove {git_changes['deleted'][0]}")
                else:
                    message_parts.append(f"remove {len(git_changes['deleted'])} files")
            
            if message_parts:
                return f"{', '.join(message_parts)} in {location}"
            else:
                return f"update files in {location}"
                
        except Exception as e:
            print(f"❌ Error creating commit message: {e}")
            return self._create_fallback_commit_message(changes, location)
    
    def _get_repo_for_location(self, location):
        """Get repository directory for a given location string"""
        if "submodule" in location:
            # Extract submodule name and find its directory
            submodule_name = location.replace("submodule ", "")
            for submodule in self.submodules:
                if submodule.name == submodule_name:
                    return submodule
        return self.repo_dir
    
    def _create_fallback_commit_message(self, changes, location):
        """Fallback commit message creation using file system events"""
        if len(changes) == 1:
            change = changes[0]
            return f"{change['event_type']} {change['file_name']} in {location}"
        
        # Group changes by type
        created_files = [c['file_name'] for c in changes if c['event_type'] == 'created']
        modified_files = [c['file_name'] for c in changes if c['event_type'] == 'modified']
        deleted_files = [c['file_name'] for c in changes if c['event_type'] == 'deleted']
        
        message_parts = []
        
        if created_files:
            if len(created_files) == 1:
                message_parts.append(f"add {created_files[0]}")
            else:
                message_parts.append(f"add {len(created_files)} files")
        
        if modified_files:
            if len(modified_files) == 1:
                message_parts.append(f"update {modified_files[0]}")
            else:
                message_parts.append(f"update {len(modified_files)} files")
        
        if deleted_files:
            if len(deleted_files) == 1:
                message_parts.append(f"remove {deleted_files[0]}")
            else:
                message_parts.append(f"remove {len(deleted_files)} files")
        
        return f"{', '.join(message_parts)} in {location}"
    
    def _push_changes(self, repo_dir, repo_name):
        """Push changes to remote repository"""
        try:
            result = self._run_git_command(['git', 'push'], repo_dir, f"Git push ({repo_name})")
            
            if result.returncode == 0:
                print(f"🚀 Pushed {repo_name} to remote")
                
        except Exception as e:
            print(f"❌ Error pushing {repo_name}: {e}")
    
    def _commit_in_submodule(self, file_path, event_type, submodule_dir):
        """Commit changes in a submodule"""
        file_name = Path(file_path).name
        commit_message = f"{event_type} {file_name}"
        
        # Run git commands in the submodule
        subprocess.run(['git', 'add', '.'], cwd=submodule_dir, check=True)
        result = subprocess.run(
            ['git', 'commit', '-m', commit_message], 
            cwd=submodule_dir, 
            capture_output=True, 
            text=True
        )
        
        if result.returncode == 0:
            print(f"✓ Submodule commit: {commit_message} in {submodule_dir.name}")
            # Push the submodule changes
            self._push_changes(submodule_dir, f"submodule {submodule_dir.name}")
        else:
            if "nothing to commit" in result.stdout:
                print(f"No changes to commit in submodule {submodule_dir.name}")
            else:
                print(f"Submodule commit failed: {result.stderr}")
    
    def _commit_submodule_update(self, submodule_dir):
        """Commit submodule update in main repo"""
        submodule_name = submodule_dir.relative_to(self.repo_dir)
        commit_message = f"Update submodule {submodule_name}"
        
        try:
            # Add the submodule update to the main repo
            add_result = self._run_git_command(
                ['git', 'add', str(submodule_name)], 
                self.repo_dir, 
                "Git add submodule update"
            )
            if add_result.returncode != 0:
                return
            
            # Commit the submodule update
            commit_result = self._run_git_command(
                ['git', 'commit', '-m', commit_message], 
                self.repo_dir, 
                "Git commit submodule update"
            )
            
            if commit_result.returncode == 0:
                print(f"✓ Main repo commit: {commit_message}")
                # Send notification for main repo commit
                self._send_commit_notification(commit_message)
                # Push the main repo changes
                self._push_changes(self.repo_dir, "main repo")
            else:
                if "nothing to commit" in commit_result.stdout:
                    print(f"No submodule changes to commit in main repo")
                    
        except Exception as e:
            print(f"❌ Error during submodule update commit: {e}")
    
    def _commit_in_main_repo(self, file_path, event_type):
        """Commit changes in main repo (non-submodule files)"""
        changed_dir = self.get_relative_directory(file_path)
        file_name = Path(file_path).name
        commit_message = f"{event_type} {file_name} in {changed_dir}"
        
        # Run git commands
        subprocess.run(['git', 'add', '.'], cwd=self.repo_dir, check=True)
        result = subprocess.run(
            ['git', 'commit', '-m', commit_message], 
            cwd=self.repo_dir, 
            capture_output=True, 
            text=True
        )
        
        if result.returncode == 0:
            print(f"✓ Committed: {commit_message}")
            # Send notification for main repo commit
            self._send_commit_notification(commit_message)
            # Push the main repo changes
            self._push_changes(self.repo_dir, "main repo")
        else:
            if "nothing to commit" in result.stdout:
                print(f"No changes to commit for {file_name}")
            else:
                print(f"Git commit failed: {result.stderr}")
    
    def on_modified(self, event):
        if not event.is_directory:
            # Check if it's a .gitignore file that changed
            if event.src_path.endswith('.gitignore'):
                print(f"📝 Gitignore file modified: {event.src_path}")
                self._load_gitignore_patterns()
                return
            
            if self._should_exclude_file(event.src_path):
                return
            print(f"📝 File modified: {event.src_path}")
            self.schedule_commit(event.src_path, "modified")
    
    def on_created(self, event):
        if not event.is_directory:
            # Check if it's a .gitignore file that was created
            if event.src_path.endswith('.gitignore'):
                print(f"📝 Gitignore file created: {event.src_path}")
                self._load_gitignore_patterns()
                # Still commit the .gitignore file itself
                self.schedule_commit(event.src_path, "created")
                return
            
            if self._should_exclude_file(event.src_path):
                return
            print(f"✨ File created: {event.src_path}")
            self.schedule_commit(event.src_path, "created")
    
    def on_deleted(self, event):
        if not event.is_directory:
            if self._should_exclude_file(event.src_path):
                return
            print(f"🗑️ File deleted: {event.src_path}")
            self.schedule_commit(event.src_path, "deleted")
    
    def cleanup(self):
        """Cancel all pending timers and execute any pending commits"""
        with self.timer_lock:
            print("🧹 Cleaning up pending commits...")
            for dir_key, timer in self.commit_timers.items():
                timer.cancel()
                # Execute the pending commit immediately
                if dir_key in self.pending_commits:
                    print(f"⚡ Executing pending commit for {Path(dir_key).name}")
                    self._execute_delayed_commit(dir_key)
            
            self.commit_timers.clear()
            self.pending_commits.clear()
            
            # Cancel fetch timer
            if self.fetch_timer:
                self.fetch_timer.cancel()
                print("🧹 Canceled fetch timer")
    
    def start_fetch_timer(self):
        """Start the periodic fetch timer"""
        self.fetch_timer = threading.Timer(FETCH_INTERVAL, self._periodic_fetch)
        self.fetch_timer.daemon = True
        self.fetch_timer.start()
        print(f"🔄 Started periodic fetch timer (every {FETCH_INTERVAL//60} minutes)")
    
    def _periodic_fetch(self):
        """Periodically fetch from remote and check for changes"""
        try:
            print("🔄 Fetching remote changes...")
            
            # Fetch and check main repo
            main_changes = self._fetch_and_check_changes(self.repo_dir, "main repo")
            
            # Fetch and check submodules
            submodule_changes = []
            for submodule in self.submodules:
                changes = self._fetch_and_check_changes(submodule, f"submodule {submodule.name}")
                if changes:
                    submodule_changes.extend(changes)
            
            # Send notifications if there are changes
            if main_changes or submodule_changes:
                self._send_change_notification(main_changes, submodule_changes)
            
        except Exception as e:
            print(f"⚠️  Error during periodic fetch: {e}")
        finally:
            # Schedule next fetch
            self.start_fetch_timer()
    
    def _fetch_and_check_changes(self, repo_dir, repo_name):
        """Fetch from remote and check for changes on any branch"""
        try:
            # Fetch from remote
            fetch_result = self._run_git_command(
                ['git', 'fetch', '--all'],
                repo_dir,
                f"Git fetch ({repo_name})"
            )
            
            if fetch_result.returncode != 0:
                return []
            
            # Get all local branches
            branches_result = self._run_git_command(
                ['git', 'branch', '--format=%(refname:short)'],
                repo_dir,
                f"Git branch list ({repo_name})"
            )
            
            if branches_result.returncode != 0:
                return []
            
            branches = branches_result.stdout.strip().split('\n')
            changes = []
            
            for branch in branches:
                if not branch.strip():
                    continue
                    
                # Check if remote branch exists
                remote_branch = f"origin/{branch}"
                remote_check = self._run_git_command(
                    ['git', 'rev-parse', '--verify', remote_branch],
                    repo_dir,
                    f"Git rev-parse ({repo_name})"
                )
                
                if remote_check.returncode != 0:
                    continue
                
                # Compare local and remote branches
                diff_result = self._run_git_command(
                    ['git', 'rev-list', '--count', f"{branch}..{remote_branch}"],
                    repo_dir,
                    f"Git rev-list ({repo_name})"
                )
                
                if diff_result.returncode == 0:
                    ahead_count = int(diff_result.stdout.strip())
                    if ahead_count > 0:
                        changes.append({
                            'branch': branch,
                            'commits': ahead_count,
                            'repo': repo_name
                        })
            
            return changes
            
        except Exception as e:
            print(f"⚠️  Error checking changes for {repo_name}: {e}")
            return []
    
    def _send_change_notification(self, main_changes, submodule_changes):
        """Send desktop notification about remote changes"""
        try:
            all_changes = main_changes + submodule_changes
            if not all_changes:
                return
            
            # Create notification message
            title = "📡 Remote Changes Detected"
            message_parts = []
            
            if main_changes:
                main_branches = [f"{c['branch']} ({c['commits']} commits)" for c in main_changes]
                message_parts.append(f"Main repo: {', '.join(main_branches)}")
            
            if submodule_changes:
                submodule_info = {}
                for change in submodule_changes:
                    repo = change['repo']
                    if repo not in submodule_info:
                        submodule_info[repo] = []
                    submodule_info[repo].append(f"{change['branch']} ({change['commits']} commits)")
                
                for repo, branches in submodule_info.items():
                    message_parts.append(f"{repo}: {', '.join(branches)}")
            
            message = '\n'.join(message_parts)
            
            # Send notification
            try:
                result = subprocess.run([
                    'notify-send',
                    '-i', 'git',
                    '-t', '10000',  # 10 second timeout
                    title,
                    message
                ], capture_output=True, text=True)
                
                if result.returncode != 0:
                    print(f"❌ Notification failed: notify-send")
                    print(f"   Return code: {result.returncode}")
                    if result.stderr.strip():
                        print(f"   STDERR: {result.stderr.strip()}")
                        
            except Exception as e:
                print(f"❌ Notification error: {e}")
            
            print(f"📡 Sent notification: {len(all_changes)} branches with changes")
            
        except Exception as e:
            print(f"⚠️  Error sending notification: {e}")
    
    def _send_commit_notification(self, commit_message):
        """Send desktop notification about a main repo commit"""
        try:
            title = "💾 Dotfiles Committed"
            
            result = subprocess.run([
                'notify-send',
                '-i', 'git',
                '-t', '5000',  # 5 second timeout
                title,
                commit_message
            ], capture_output=True, text=True)
            
            if result.returncode != 0:
                print(f"❌ Commit notification failed: notify-send")
                print(f"   Return code: {result.returncode}")
                if result.stderr.strip():
                    print(f"   STDERR: {result.stderr.strip()}")
                    
        except Exception as e:
            print(f"❌ Commit notification error: {e}")
    
    def _commit_existing_changes(self):
        """Check for and commit any existing uncommitted changes on startup"""
        print("🔍 Checking for existing uncommitted changes...")
        
        # Check main repo
        self._commit_existing_in_repo(self.repo_dir, "main repo")
        
        # Check all submodules
        for submodule in self.submodules:
            submodule_name = submodule.name
            self._commit_existing_in_repo(submodule, f"submodule {submodule_name}")
    
    def _commit_existing_in_repo(self, repo_dir, repo_name):
        """Check for and commit existing changes in a specific repository"""
        try:
            # Check for staged changes
            staged_result = self._run_git_command(
                ['git', 'diff', '--cached', '--name-only'],
                repo_dir,
                f"Check staged changes ({repo_name})"
            )
            
            # Check for unstaged changes
            unstaged_result = self._run_git_command(
                ['git', 'diff', '--name-only'],
                repo_dir,
                f"Check unstaged changes ({repo_name})"
            )
            
            # Check for untracked files (excluding ignored ones)
            untracked_result = self._run_git_command(
                ['git', 'ls-files', '--others', '--exclude-standard'],
                repo_dir,
                f"Check untracked files ({repo_name})"
            )
            
            has_staged = staged_result.returncode == 0 and staged_result.stdout.strip()
            has_unstaged = unstaged_result.returncode == 0 and unstaged_result.stdout.strip()
            has_untracked = untracked_result.returncode == 0 and untracked_result.stdout.strip()
            
            if has_staged or has_unstaged or has_untracked:
                print(f"📝 Found uncommitted changes in {repo_name}")
                
                # Add all changes (staged, unstaged, and untracked)
                add_result = self._run_git_command(['git', 'add', '.'], repo_dir, f"Git add existing changes ({repo_name})")
                if add_result.returncode != 0:
                    return
                
                # Create commit message based on what we found
                change_types = []
                if has_staged:
                    change_types.append("staged changes")
                if has_unstaged:
                    change_types.append("unstaged changes")
                if has_untracked:
                    change_types.append("untracked files")
                
                commit_message = f"startup commit: {', '.join(change_types)} in {repo_name}"
                
                # Commit the changes
                commit_result = self._run_git_command(
                    ['git', 'commit', '-m', commit_message],
                    repo_dir,
                    f"Git commit existing changes ({repo_name})"
                )
                
                if commit_result.returncode == 0:
                    print(f"✓ Startup commit: {commit_message}")
                    
                    # Send notification for main repo commits
                    if repo_dir == self.repo_dir:
                        self._send_commit_notification(commit_message)
                    
                    # Push the changes
                    if repo_dir == self.repo_dir:
                        self._push_changes(repo_dir, "main repo")
                    else:
                        self._push_changes(repo_dir, f"submodule {repo_dir.name}")
                        # Also update the submodule reference in main repo
                        self._commit_submodule_update(repo_dir)
                        
            else:
                print(f"✓ No uncommitted changes found in {repo_name}")
                
        except Exception as e:
            print(f"❌ Error checking existing changes in {repo_name}: {e}")

def main():
    print(f"🚀 Starting dotfiles watcher v2.0")
    print(f"📅 {time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Expand the watch directory path
    watch_dir = Path(WATCH_DIRECTORY).expanduser().resolve()
    
    # Validate directory exists
    if not watch_dir.exists():
        print(f"❌ Error: Directory {watch_dir} does not exist")
        return
    
    # Check if it's a git repository
    if not (Path(REPO_DIRECTORY) / '.git').exists():
        print(f"❌ Error: {REPO_DIRECTORY} is not a git repository")
        return
    
    print(f"📁 Watching directory: {watch_dir}")
    print(f"📦 Git repository: {REPO_DIRECTORY}")
    print("⏰ Starting file monitoring...")
    
    # Set up file watcher
    event_handler = GitCommitHandler(watch_dir, REPO_DIRECTORY)
    observer = Observer()
    observer.schedule(event_handler, str(watch_dir), recursive=True)
    observer.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Stopping file watcher...")
        observer.stop()
        event_handler.cleanup()
    
    observer.join()
    print("✅ File watcher stopped.")

if __name__ == "__main__":
    main()
