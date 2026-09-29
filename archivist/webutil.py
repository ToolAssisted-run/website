"""The JSON error shape every endpoint answers with."""
import base64
import hashlib
import hmac
import json
import logging
import os
import pathlib
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import jsonify


def fail(msg, code=400):
    return jsonify({'ok': False, 'error': msg}), code

