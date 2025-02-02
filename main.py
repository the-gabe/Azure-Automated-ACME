#!/usr/bin/env python3

# The MIT License (MIT)
#
# Copyright © 2015 Daniel Roesler
# Copyright © 2024 Adappt Limited
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

# This python script contains derivative works taken from https://github.com/diafygi/acme-tiny @ c29c0f36cedbca2a7117169c6a9e1f166c501899

import subprocess, json, os, base64, binascii, time, hashlib, re, logging, os
from urllib.request import urlopen, Request
from datetime import datetime, timedelta, timezone
from urllib.error import URLError, HTTPError

class BlobStorageAuth:
    def __init__(self):
        self.token = None
        self.token_expires_at = None
    def get_access_token(self):
        # Keep same access token if it has not expired yet, since they have a 1 hour lifetime
        if self.token and self.token_expires_at > datetime.now(timezone.utc) + timedelta(minutes=5):
            return self.token
        endpoint = os.getenv('IDENTITY_ENDPOINT')
        identity_header = os.getenv('IDENTITY_HEADER')
        resource_url = f"{endpoint}?resource=https://storage.azure.com/&api-version=2019-08-01"
        headers = {
            'X-IDENTITY-HEADER': identity_header,
            'Metadata': 'true',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        try:
            req = Request(resource_url, headers=headers, method='GET')
            response = urlopen(req, timeout=30)
            if response.status != 200:
                raise Exception(f"Failed to obtain token. Status code: {response.status}")
            response_data = json.loads(response.read())
            self.token = response_data['access_token']
            self.token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
            return self.token
        except URLError as e:
            raise Exception(f"Failed to obtain token: {str(e)}")

class BlobStorageClient:
    def __init__(self, storage_account_name):
        self.storage_account_name = storage_account_name
        self.auth = BlobStorageAuth()
        self.base_url = f"https://{storage_account_name}.blob.core.windows.net"
    def upload_blob(self, container_name, blob_name, content):
        token = self.auth.get_access_token()
        headers = {
            'Authorization': f'Bearer {token}',
            'x-ms-version': '2021-08-06',
            'x-ms-date': datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT'),
            'Content-Type': 'application/octet-stream',
            'Content-Length': str(len(content)),
            'x-ms-blob-type': 'BlockBlob'
        }
        url = f"{self.base_url}/{container_name}/{blob_name}"
        req = Request(url, headers=headers, method='PUT', data=content)
        urlopen(req)
        return True
    def delete_blob(self, container_name, blob_name):
        token = self.auth.get_access_token()
        headers = {
            'Authorization': f'Bearer {token}',
            'x-ms-version': '2021-08-06',
            'x-ms-date': datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT'),
            'x-ms-delete-snapshots': 'include'
        }
        url = f"{self.base_url}/{container_name}/{blob_name}"
        req = Request(url, headers=headers, method='DELETE')
        urlopen(req)
        return True

class KeyVaultAuth:
    def __init__(self):
        self.token = None
        self.token_expires_at = None
    def get_access_token(self):
        if self.token and self.token_expires_at > datetime.utcnow() + timedelta(minutes=5):
            return self.token
        endpoint = os.getenv('IDENTITY_ENDPOINT')
        identity_header = os.getenv('IDENTITY_HEADER')
        resource_url = f"{endpoint}?resource=https://vault.azure.net/&api-version=2019-08-01"
        headers = {
            'X-IDENTITY-HEADER': identity_header,
            'Metadata': 'true',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        try:
            req = Request(resource_url, headers=headers, method='GET')
            response = urlopen(req, timeout=30)
            if response.status != 200:
                raise Exception(f"Failed to obtain token. Status code: {response.status}")
            response_data = json.loads(response.read())
            self.token = response_data['access_token']
            self.token_expires_at = datetime.utcnow() + timedelta(hours=1)
            return self.token
        except URLError as e:
            raise Exception(f"Failed to obtain token: {str(e)}")

class KeyVaultClient:
    def __init__(self, key_vault_name):
        self.key_vault_name = key_vault_name
        self.auth = KeyVaultAuth()
        self.base_url = f"https://{key_vault_name}.vault.azure.net"
        self.api_version = "7.4"
    def set_secret(self, secret_name, secret_value, expiry_time):
        token = self.auth.get_access_token()
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        }
        url = f"{self.base_url}/secrets/{secret_name}?api-version={self.api_version}"
        if expiry_time != "never":
            json_data_attributes = {"enabled":"true","exp": expiry_time}
            # We always set the contentType here because only TLS certificates used on Application Gateway ever have an expiry time
            json_data = {"value": secret_value, "contentType":"application/x-pkcs12","attributes": json_data_attributes}
        else:
            json_data = {"value": secret_value, "contentType":"application/pkcs8"}
        encoded_json_data = json.dumps(json_data).encode()
        req = Request(url, headers=headers, method='PUT', data=encoded_json_data)
        urlopen(req)
        return True
    def get_latest_secret_value(self, secret_name):
        token = self.auth.get_access_token()
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        }
        url = f"{self.base_url}/secrets/{secret_name}?api-version={self.api_version}"
        req = Request(url, headers=headers, method='GET')
        response = urlopen(req)
        parsed_json = json.loads(response.read().decode())
        return parsed_json['value']

ACCOUNT_KEY_SECRET_NAME = "ACME-accountkey"
TLS_CERT_SECRET_NAME = "ACME-tlscert"
ACCOUNT_KEY_PATH = "/tmp/thisistheaccount.key"
CSR_PATH="/tmp/thisisthe.csr"
DOMAIN_PRIVATE_KEY_PATH="/tmp/thisisthedomainprivate.key"
PFX_PATH="/tmp/thisisthe.pfx"
PEM_PATH="/tmp/thisisthesignedcert.pem"
DEFAULT_DIRECTORY_URL = os.environ.get("ACME_DIRECTORY_URL")
BLOB_STORAGE_NAME = os.environ.get("ACME_BLOB_STORAGE_NAME")
CONTACT_EMAIL = os.environ.get("ACME_CONTACT_EMAIL")
KEYVAULT_NAME = os.environ.get("ACME_KEYVAULT_NAME")
COMMON_NAME = os.environ.get("ACME_COMMON_NAME")
LOGGER = logging.getLogger(__name__)
LOGGER.addHandler(logging.StreamHandler())
LOGGER.setLevel(logging.INFO)

def get_crt(azure_keyvault_name=KEYVAULT_NAME, log=LOGGER, directory_url=DEFAULT_DIRECTORY_URL, contact=CONTACT_EMAIL):

    # Global variable init
    directory, acct_headers, alg, jwk = None, None, None, None

    # helper functions - base64 encode for jose spec
    def _b64(b):
        return base64.urlsafe_b64encode(b).decode('utf8').replace("=", "")

    # helper function - run external commands
    def _cmd(cmd_list, stdin=None, cmd_input=None, err_msg="Command Line Error"):
        proc = subprocess.Popen(cmd_list, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = proc.communicate(cmd_input)
        if proc.returncode != 0:
            raise IOError("{0}\n{1}".format(err_msg, err))
        return out

    # Fetch account key from Azure KeyVault, if one does not exist, generate one and store it there
    try:
        keyvault_client = KeyVaultClient(azure_keyvault_name)
        account_key_value = keyvault_client.get_latest_secret_value(secret_name=ACCOUNT_KEY_SECRET_NAME)

    except HTTPError as e:
            if e.code == 404:
                print("Secret for account key was not found, making new account key")
                try:
                    account_key_value = _cmd(["openssl", "genrsa", "4096"], err_msg="OpenSSL Error")
                    with open(ACCOUNT_KEY_PATH, "wb") as file:
                        file.write(account_key_value)
                except:
                    print("Something has gone very wrong whilst trying to generate a new account key")
                    exit()
                finally:
                    print("Generated new account key with OpenSSL")
                    with open(ACCOUNT_KEY_PATH, "r") as file:
                        account_key_value = file.read()
                    success = keyvault_client.set_secret(secret_name=ACCOUNT_KEY_SECRET_NAME,secret_value=account_key_value,expiry_time="never")
            else:
                print("Something has gone very wrong whilst trying to fetch the account key from keyvault")
                print("The error is: ",e)
                exit()
    finally:
        # Write account key value, be it obtained via Azure Key Vault or generated
        with open(ACCOUNT_KEY_PATH, "w") as file:
            file.write(account_key_value)


    # Generate domain private key and CSR with OpenSSL
    try:
        # Run OpenSSL command to generate CSR and domain key
        domain_private_key = _cmd(["openssl", "genrsa", "4096"], err_msg="OpenSSL Error")
        with open(DOMAIN_PRIVATE_KEY_PATH, "wb") as file:
            file.write(domain_private_key)
        
        cn = "/CN="+COMMON_NAME
        csr_value = _cmd(["openssl", "req", "-new", "-sha256", "-key", DOMAIN_PRIVATE_KEY_PATH, "-subj", cn], err_msg="OpenSSL Error")
        with open(CSR_PATH, "wb") as file:
            file.write(csr_value)
    except:
        print("Something has gone very wrong whilst trying to generate a new CSR or domain private key")
        exit()
    finally:
        print("Generated new CSR and domain private key with OpenSSL")

    # helper function - make request and automatically parse json response
    def _do_request(url, data=None, err_msg="Error", depth=0):
        try:
            resp = urlopen(Request(url, data=data, headers={"Content-Type": "application/jose+json", "User-Agent": "Better-AGW-ACME"}))
            resp_data, code, headers = resp.read().decode("utf8"), resp.getcode(), resp.headers
        except IOError as e:
            resp_data = e.read().decode("utf8") if hasattr(e, "read") else str(e)
            code, headers = getattr(e, "code", None), {}
        try:
            resp_data = json.loads(resp_data) # try to parse json results
        except ValueError:
            pass # ignore json parsing errors
        if depth < 100 and code == 400 and resp_data['type'] == "urn:ietf:params:acme:error:badNonce":
            raise IndexError(resp_data) # allow 100 retrys for bad nonces
        if code not in [200, 201, 204]:
            raise ValueError("{0}:\nUrl: {1}\nData: {2}\nResponse Code: {3}\nResponse: {4}".format(err_msg, url, data, code, resp_data))
        return resp_data, code, headers

    # helper function - make signed requests
    def _send_signed_request(url, payload, err_msg, depth=0):
        payload64 = "" if payload is None else _b64(json.dumps(payload).encode('utf8'))
        new_nonce = _do_request(directory['newNonce'])[2]['Replay-Nonce']
        protected = {"url": url, "alg": alg, "nonce": new_nonce}
        protected.update({"jwk": jwk} if acct_headers is None else {"kid": acct_headers['Location']})
        protected64 = _b64(json.dumps(protected).encode('utf8'))
        protected_input = "{0}.{1}".format(protected64, payload64).encode('utf8')
        out = _cmd(["openssl", "dgst", "-sha256", "-sign", ACCOUNT_KEY_PATH], stdin=subprocess.PIPE, cmd_input=protected_input, err_msg="OpenSSL Error")
        data = json.dumps({"protected": protected64, "payload": payload64, "signature": _b64(out)})
        try:
            return _do_request(url, data=data.encode('utf8'), err_msg=err_msg, depth=depth)
        except IndexError: # retry bad nonces (they raise IndexError)
            return _send_signed_request(url, payload, err_msg, depth=(depth + 1))

    # helper function - poll until complete
    def _poll_until_not(url, pending_statuses, err_msg):
        result, t0 = None, time.time()
        while result is None or result['status'] in pending_statuses:
            assert (time.time() - t0 < 3600), "Polling timeout" # 1 hour timeout
            time.sleep(0 if result is None else 2)
            result, _, _ = _send_signed_request(url, None, err_msg)
        return result

    # parse account key to get public key
    log.info("Parsing account key...")
    out = _cmd(["openssl", "rsa", "-in", ACCOUNT_KEY_PATH, "-noout", "-text"], err_msg="OpenSSL Error")
    pub_pattern = r"modulus:[\s]+?00:([a-f0-9\:\s]+?)\npublicExponent: ([0-9]+)"
    pub_hex, pub_exp = re.search(pub_pattern, out.decode('utf8'), re.MULTILINE|re.DOTALL).groups()
    pub_exp = "{0:x}".format(int(pub_exp))
    pub_exp = "0{0}".format(pub_exp) if len(pub_exp) % 2 else pub_exp
    alg, jwk = "RS256", {
        "e": _b64(binascii.unhexlify(pub_exp.encode("utf-8"))),
        "kty": "RSA",
        "n": _b64(binascii.unhexlify(re.sub(r"(\s|:)", "", pub_hex).encode("utf-8"))),
    }
    accountkey_json = json.dumps(jwk, sort_keys=True, separators=(',', ':'))
    thumbprint = _b64(hashlib.sha256(accountkey_json.encode('utf8')).digest())

    # find domains
    log.info("Parsing CSR...")
    out = _cmd(["openssl", "req", "-in", CSR_PATH, "-noout", "-text"], err_msg="Error loading {0}".format(CSR_PATH))
    domains = set([])
    common_name = re.search(r"Subject:.*? CN\s?=\s?([^\s,;/]+)", out.decode('utf8'))
    if common_name is not None:
        domains.add(common_name.group(1))
    subject_alt_names = re.search(r"X509v3 Subject Alternative Name: (?:critical)?\n +([^\n]+)\n", out.decode('utf8'), re.MULTILINE|re.DOTALL)
    if subject_alt_names is not None:
        for san in subject_alt_names.group(1).split(", "):
            if san.startswith("DNS:"):
                domains.add(san[4:])
    log.info(u"Found domains: {0}".format(", ".join(domains)))

    # get the ACME directory of urls
    log.info("Getting directory...")
    directory, _, _ = _do_request(directory_url, err_msg="Error getting directory")
    log.info("Directory found!")

    # create account, update contact details (if any), and set the global key identifier
    log.info("Registering account...")
    reg_payload = {"termsOfServiceAgreed": True} if contact is None else {"termsOfServiceAgreed": True, "contact": [contact,]}
    account, code, acct_headers = _send_signed_request(directory['newAccount'], reg_payload, "Error registering")
    log.info("{0} Account ID: {1}".format("Registered!" if code == 201 else "Already registered!", acct_headers['Location']))
    if contact is not None:
        account, _, _ = _send_signed_request(acct_headers['Location'], {"contact": [contact,]}, "Error updating contact details")
        log.info("Updated contact details:\n{0}".format("\n".join(account['contact'])))

    # create a new order
    log.info("Creating new order...")
    order_payload = {"identifiers": [{"type": "dns", "value": d} for d in domains]}
    order, _, order_headers = _send_signed_request(directory['newOrder'], order_payload, "Error creating new order")
    log.info("Order created!")

    # get the authorizations that need to be completed
    for auth_url in order['authorizations']:
        authorization, _, _ = _send_signed_request(auth_url, None, "Error getting challenges")
        domain = authorization['identifier']['value']

        # skip if already valid
        if authorization['status'] == "valid":
            log.info("Already verified: {0}, skipping...".format(domain))
            continue
        log.info("Verifying {0}...".format(domain))

        # find the http-01 challenge and write the challenge file
        challenge = [c for c in authorization['challenges'] if c['type'] == "http-01"][0]
        token = re.sub(r"[^A-Za-z0-9_\-]", "_", challenge['token'])
        keyauthorization = "{0}.{1}".format(token, thumbprint)

        # Upload ACME challenge TXT to Blob Storage
        storage_client = BlobStorageClient(BLOB_STORAGE_NAME)
        blob_name=".well-known/acme-challenge/"+token
        keyauthorization = bytes(keyauthorization,"utf-8")
        success = storage_client.upload_blob(container_name="$web",blob_name=blob_name,content=keyauthorization)
        print(f"Upload successful for ACME Challenge file: {success}")

        # say the challenge is done
        _send_signed_request(challenge['url'], {}, "Error submitting challenges: {0}".format(domain))
        authorization = _poll_until_not(auth_url, ["pending"], "Error checking challenge status for {0}".format(domain))
        if authorization['status'] != "valid":
            raise ValueError("Challenge did not pass for {0}: {1}".format(domain, authorization))
        
        success = storage_client.delete_blob(container_name="$web",blob_name=blob_name)
        print(f"Delete successful for ACME Challenge file as we are done with it now: {success}") 
        log.info("{0} verified!".format(domain))

    # finalize the order with the csr
    log.info("Signing certificate...")
    csr_der = _cmd(["openssl", "req", "-in", CSR_PATH, "-outform", "DER"], err_msg="DER Export Error")
    _send_signed_request(order['finalize'], {"csr": _b64(csr_der)}, "Error finalizing order")

    # poll the order to monitor when it's done
    order = _poll_until_not(order_headers['Location'], ["pending", "processing"], "Error checking order status")
    if order['status'] != "valid":
        raise ValueError("Order failed: {0}".format(order))

    # download the certificate
    certificate_pem, _, _ = _send_signed_request(order['certificate'], None, "Certificate download failed")
    log.info("Certificate signed!")

    # Write to file because OpenSSL is annoying
    with open(PEM_PATH, "w") as file:
        file.write(certificate_pem)
    # convert to pfx
    _cmd(["openssl", "pkcs12", "-keypbe", "NONE", "-certpbe", "NONE", "-inkey", DOMAIN_PRIVATE_KEY_PATH, "-in", PEM_PATH, "-export", "-out", PFX_PATH], err_msg="OpenSSL Error")

    with open(PFX_PATH, "rb") as file:
        pfx = file.read()
    
    try:
        os.remove(DOMAIN_PRIVATE_KEY_PATH)
        os.remove(CSR_PATH)
        os.remove(ACCOUNT_KEY_PATH)
        os.remove(PFX_PATH)
        os.remove(PEM_PATH)
    except:
        print("Error: Unable to fully clean up all files")
    finally:
        print("Finished with all ACME processes and cleanup")
    return str(pfx)

def main():
    # Go through the whole ACME flow, this deals with the account key, CSR, acme-challenge, removal of the ACME challenge and cleanup.
    signed_crt = get_crt()
    key_vault_client = KeyVaultClient(KEYVAULT_NAME)
    future_date = datetime.utcnow() + timedelta(days=90) # TLS certificates only have a max lifetime of 90 days
    future_unix_time = int(future_date.timestamp())
    success = key_vault_client.set_secret(secret_name=TLS_CERT_SECRET_NAME,secret_value=signed_crt,expiry_time=future_unix_time)
    print("We have finished everything!", success)
    # TODO: We now need to tag the Azure Application Gateway, so that way it will use the new cert immediately
    # TODO: We now need to revoke any certificates which remain in Azure Key Vault, and set them to expire in the next 10 minutes
main()
