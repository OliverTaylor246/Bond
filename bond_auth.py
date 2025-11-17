"""
Authentication module for Bond CLI.
Handles login, token storage, and session management.
"""
import json
import os
import webbrowser
from pathlib import Path
from typing import Optional
import httpx
from rich.console import Console

console = Console()

# Local token storage
AUTH_DIR = Path.home() / ".bond"
TOKEN_FILE = AUTH_DIR / "auth.json"


class BondAuth:
  """Handles authentication for Bond CLI."""

  def __init__(self, api_url: str):
    self.api_url = api_url.rstrip("/")
    self.token: Optional[str] = None
    self.user_email: Optional[str] = None

    # Ensure auth directory exists
    AUTH_DIR.mkdir(exist_ok=True)

    # Load existing token if available
    self.load_token()

  def load_token(self) -> bool:
    """Load saved authentication token."""
    if not TOKEN_FILE.exists():
      return False

    try:
      with open(TOKEN_FILE, "r") as f:
        data = json.load(f)
        self.token = data.get("access_token")
        self.user_email = data.get("email")
        return bool(self.token)
    except Exception as e:
      console.print(f"[dim red]Error loading token: {e}[/]")
      return False

  def save_token(self, access_token: str, email: str) -> None:
    """Save authentication token locally."""
    data = {
      "access_token": access_token,
      "email": email
    }

    try:
      with open(TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)

      # Secure the file (user read/write only)
      os.chmod(TOKEN_FILE, 0o600)

      self.token = access_token
      self.user_email = email
    except Exception as e:
      console.print(f"[red]Error saving token: {e}[/]")

  def clear_token(self) -> None:
    """Clear saved authentication token."""
    if TOKEN_FILE.exists():
      TOKEN_FILE.unlink()

    self.token = None
    self.user_email = None

  def is_authenticated(self) -> bool:
    """Check if user is authenticated."""
    return bool(self.token)

  async def login(self) -> bool:
    """
    Login via browser OAuth flow.

    Opens browser to authenticate, waits for token callback.
    """
    console.print("\n[bold cyan]🔐 Bond Login[/]\n")
    console.print("Opening your browser for authentication...")
    console.print(f"[dim]If browser doesn't open, visit: {self.api_url}/login/cli[/]\n")

    # Open CLI login page in browser
    login_url = f"{self.api_url}/login/cli"

    # Suppress webbrowser errors and try to open in Windows browser from WSL
    import subprocess
    browser_opened = False

    try:
      # Try opening via Windows from WSL
      subprocess.run(["cmd.exe", "/c", "start", login_url], stderr=subprocess.DEVNULL, timeout=2)
      browser_opened = True
    except Exception:
      pass

    if not browser_opened:
      console.print(f"\n[cyan]Please open this URL in your browser:[/]")
      console.print(f"[bold cyan]{login_url}[/]\n")

    # Prompt user to paste token
    console.print("[yellow]After logging in:[/]")
    console.print("1. The page will show your access token")
    console.print("2. Click 'Copy Token' button")
    console.print("3. Paste it below\n")

    token = console.input("[bold green]Paste your access token:[/] ").strip()

    if not token:
      console.print("[red]No token provided[/]")
      return False

    # Verify token by calling API
    try:
      async with httpx.AsyncClient() as client:
        response = await client.get(
          f"{self.api_url}/v1/user/stats",
          headers={"Authorization": f"Bearer {token}"}
        )

        if response.status_code == 200:
          user_data = response.json()
          email = user_data.get("email", "unknown")

          self.save_token(token, email)
          console.print(f"\n[green]✓ Logged in as {email}[/]\n")
          return True
        else:
          console.print(f"[red]Invalid token: {response.status_code}[/]")
          return False

    except Exception as e:
      console.print(f"[red]Login failed: {e}[/]")
      return False

  async def login_with_email_password(self, email: str, password: str) -> bool:
    """
    Login with email and password (alternative to OAuth).

    Args:
      email: User email
      password: User password

    Returns:
      True if login successful
    """
    try:
      async with httpx.AsyncClient() as client:
        # Call Supabase auth endpoint via Bond API
        response = await client.post(
          f"{self.api_url}/auth/login",
          json={"email": email, "password": password}
        )

        if response.status_code == 200:
          data = response.json()
          token = data.get("access_token")

          if token:
            self.save_token(token, email)
            console.print(f"\n[green]✓ Logged in as {email}[/]\n")
            return True

        console.print(f"[red]Login failed: {response.status_code}[/]")
        return False

    except Exception as e:
      console.print(f"[red]Login failed: {e}[/]")
      return False

  def get_auth_headers(self) -> dict[str, str]:
    """Get authentication headers for API requests."""
    if self.token:
      return {"Authorization": f"Bearer {self.token}"}
    return {}

  async def logout(self) -> None:
    """Logout and clear stored token."""
    self.clear_token()
    console.print("[green]✓ Logged out successfully[/]")

  async def check_session(self) -> bool:
    """Verify the current session is still valid."""
    if not self.token:
      return False

    try:
      async with httpx.AsyncClient() as client:
        response = await client.get(
          f"{self.api_url}/v1/user/stats",
          headers=self.get_auth_headers()
        )
        return response.status_code == 200
    except Exception:
      return False
