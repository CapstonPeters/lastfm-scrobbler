import hashlib, time, requests, json

api_key = "807a46aa6d93bdf4ffa6a824cd2ceb40"
secret = "9d16aabf3ddf319db08a5b710db5941d"
sk = "sEmquTxvzeB10Ysr5oyfYx9IRyKW-j84"

params = {
    "method": "track.scrobble",
    "api_key": api_key,
    "sk": sk,
    "artist[0]": "Test Artist",
    "track[0]": "Clean Song",
    "timestamp[0]": str(int(time.time())),
}

sorted_keys = sorted(params.keys())
raw = "".join(f"{k}{params[k]}" for k in sorted_keys)
raw += secret
params["api_sig"] = hashlib.md5(raw.encode()).hexdigest()
params["format"] = "json"

print("POST params:", json.dumps({k: v for k,v in params.items() if k != "api_sig"}, indent=2))
print("api_sig:", params["api_sig"])

resp = requests.post("https://ws.audioscrobbler.com/2.0/", data=params, timeout=30)
print(f"\nStatus: {resp.status_code}")
print(f"Response:\n{json.dumps(resp.json(), indent=2)}")
