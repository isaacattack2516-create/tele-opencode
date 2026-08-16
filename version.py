#!/usr/bin/env python3
"""Version information for Tele-OpenCode.

Bump VERSION on every release. The schema/config gets new fields additively,
and older clients must keep working when talking to newer ones and vice versa
(two-releases-back compatibility): always add, never remove or rename fields,
and always read with .get() defaults.
"""

VERSION = "2.0.0"
