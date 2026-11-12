# Shared VHS settings for every terminal scene (palette = dashboard dark theme, see templates/base.css).
# Every scene tape starts with: Source submission/media/tapes/settings.tape  (VHS runs from the repo root)
Set Shell "bash"
Set FontFamily "JetBrains Mono"
Set FontSize 22
Set Width 1920
Set Height 1080
Set Padding 40
Set TypingSpeed 45ms
Set WindowBar Colorful
Set CursorBlink false
Set Theme { "name": "anchorium", "background": "#0f1216", "foreground": "#e8ebef", "selection": "#283039", "cursor": "#e8ebef", "black": "#171b21", "red": "#f97066", "green": "#4cc38a", "yellow": "#f5b546", "blue": "#7c9cff", "magenta": "#c59cff", "cyan": "#6fd3e0", "white": "#e8ebef", "brightBlack": "#98a1ad", "brightRed": "#f97066", "brightGreen": "#4cc38a", "brightYellow": "#f5b546", "brightBlue": "#7c9cff", "brightMagenta": "#c59cff", "brightCyan": "#6fd3e0", "brightWhite": "#ffffff" }
# A neutral prompt: no user name, host or home path on screen.
Hide
Type "export PS1='$ ' PROMPT_COMMAND='' BASH_SILENCE_DEPRECATION_WARNING=1 && clear"
Enter
Sleep 300ms
Show
