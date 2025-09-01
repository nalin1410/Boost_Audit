import base64
import requests
import os
from datetime import datetime, timedelta
import json
import io
from PIL import Image
import tempfile
import urllib.parse
import time


class OneDriveUploader:
    def __init__(self):
        # You'll need to set these environment variables or configure them
        self.client_id = os.getenv('ONEDRIVE_CLIENT_ID')
        self.client_secret = os.getenv('ONEDRIVE_CLIENT_SECRET')
        self.tenant_id = os.getenv('ONEDRIVE_TENANT_ID')
        self.refresh_token = os.getenv('ONEDRIVE_REFRESH_TOKEN')

        # OneDrive API endpoints - using tenant-specific endpoint
        self.token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        self.upload_url_base = "https://graph.microsoft.com/v1.0/me/drive/root:/MysteryAudits"
        self.graph_api_base = "https://graph.microsoft.com/v1.0"

        # Token cache
        self.access_token = None
        self.token_expires_at = None

    def get_access_token(self):
        """Get or refresh the access token"""
        try:
            # Check if current token is still valid
            if self.access_token and self.token_expires_at and datetime.now() < self.token_expires_at:
                return self.access_token

            print("🔄 Refreshing OneDrive access token...")

            data = {
                'grant_type': 'refresh_token',
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'refresh_token': self.refresh_token,
                'scope': 'https://graph.microsoft.com/Files.ReadWrite'
            }

            response = requests.post(self.token_url, data=data)
            response.raise_for_status()

            token_data = response.json()
            self.access_token = token_data['access_token']

            # Set expiration time (subtract 5 minutes for safety)
            expires_in = token_data.get('expires_in', 3600)
            self.token_expires_at = datetime.now() + timedelta(seconds=expires_in - 300)

            print("✅ OneDrive access token refreshed successfully")
            return self.access_token

        except Exception as e:
            print(f"❌ Error getting OneDrive access token: {str(e)}")
            raise Exception(f"Failed to get OneDrive access token: {str(e)}")

    def upload_file(self, file_data, filename, folder_path=""):
        """Upload file to OneDrive"""
        try:
            access_token = self.get_access_token()

            # Prepare the upload URL
            if folder_path:
                upload_url = f"{self.upload_url_base}/{folder_path}/{filename}:/content"
            else:
                upload_url = f"{self.upload_url_base}/{filename}:/content"

            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/octet-stream'
            }

            print(f"📤 Uploading {filename} to OneDrive...")

            # If file_data is base64, decode it
            if isinstance(file_data, str):
                if file_data.startswith('data:image/'):
                    # Remove data URL prefix
                    file_data = file_data.split(',')[1]
                file_bytes = base64.b64decode(file_data)
            else:
                file_bytes = file_data

            # Upload the file
            response = requests.put(upload_url, headers=headers, data=file_bytes)
            response.raise_for_status()

            upload_result = response.json()
            file_id = upload_result.get('id')
            file_name = upload_result.get('name')

            print(f"✅ File uploaded successfully. File ID: {file_id}")

            # Return file metadata including download URL
            return {
                'file_id': file_id,
                'file_name': file_name,
                'download_url': upload_result.get('@microsoft.graph.downloadUrl'),
                'web_url': upload_result.get('webUrl')
            }

        except Exception as e:
            print(f"❌ Error uploading to OneDrive: {str(e)}")
            raise Exception(f"OneDrive upload failed: {str(e)}")

    def resolve_sharepoint_sharing_url(self, sharing_url):
        """
        Convert SharePoint sharing URL to actual file ID using Microsoft Graph API
        """
        try:
            access_token = self.get_access_token()

            # Encode the sharing URL
            encoded_url = base64.b64encode(sharing_url.encode('utf-8')).decode('utf-8')
            # Remove padding and replace characters for URL-safe base64
            encoded_url = encoded_url.rstrip('=').replace('+', '-').replace('/', '_')

            # Use the shares endpoint to resolve the sharing URL
            shares_url = f"https://graph.microsoft.com/v1.0/shares/u!{encoded_url}/driveItem"

            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }

            print(f"🔍 Resolving SharePoint sharing URL: {sharing_url}")

            response = requests.get(shares_url, headers=headers)
            response.raise_for_status()

            file_data = response.json()
            file_id = file_data.get('id')

            print(f"✅ Resolved SharePoint URL to file ID: {file_id}")

            return {
                'file_id': file_id,
                'file_name': file_data.get('name'),
                'size': file_data.get('size'),
                'created_datetime': file_data.get('createdDateTime'),
                'modified_datetime': file_data.get('lastModifiedDateTime')
            }

        except Exception as e:
            print(f"❌ Error resolving SharePoint sharing URL: {str(e)}")
            return None

    def validate_file_id(self, file_id):
        """Validate if a file ID exists and is accessible"""
        try:
            access_token = self.get_access_token()

            # Try different API endpoints to locate the file
            endpoints_to_try = [
                f"{self.graph_api_base}/me/drive/items/{file_id}",
                f"{self.graph_api_base}/drives/me/items/{file_id}",
            ]

            headers = {'Authorization': f'Bearer {access_token}'}

            for endpoint in endpoints_to_try:
                print(f"🔍 Trying endpoint: {endpoint}")
                response = requests.get(endpoint, headers=headers)

                if response.status_code == 200:
                    file_data = response.json()
                    print(f"✅ File found via {endpoint}")
                    return {
                        'exists': True,
                        'file_data': file_data,
                        'endpoint_used': endpoint
                    }
                else:
                    print(f"❌ {endpoint} returned {response.status_code}: {response.text}")

            return {'exists': False, 'error': 'File not found in any endpoint'}

        except Exception as e:
            print(f"❌ Error validating file ID: {str(e)}")
            return {'exists': False, 'error': str(e)}

    def get_file_download_url(self, file_id):
        """Get direct download URL for a file with enhanced error handling"""
        try:
            access_token = self.get_access_token()

            headers = {
                'Authorization': f'Bearer {access_token}'
            }

            # Get file metadata including download URL
            url = f"{self.graph_api_base}/me/drive/items/{file_id}"
            response = requests.get(url, headers=headers)
            response.raise_for_status()

            file_data = response.json()
            download_url = file_data.get('@microsoft.graph.downloadUrl')

            if download_url:
                return download_url

            # Fallback: try content endpoint
            content_url = f"{self.graph_api_base}/me/drive/items/{file_id}/content"
            content_response = requests.head(content_url, headers=headers, allow_redirects=False)

            if content_response.status_code == 302:
                return content_response.headers.get('Location')

            raise Exception("No download URL available")

        except Exception as e:
            print(f"❌ Error getting download URL: {str(e)}")
            raise e

    def download_file_content(self, file_id):
        """Download file content from OneDrive"""
        try:
            access_token = self.get_access_token()

            headers = {
                'Authorization': f'Bearer {access_token}'
            }

            # Get download URL first
            download_url = self.get_file_download_url(file_id)
            if not download_url:
                raise Exception("Could not get download URL")

            # Download the file content
            response = requests.get(download_url, headers=headers, timeout=30)
            response.raise_for_status()

            return response.content

        except Exception as e:
            print(f"❌ Error downloading file content: {str(e)}")
            raise Exception(f"Failed to download file: {str(e)}")

    def get_file_content_from_sharing_url(self, sharing_url):
        """
        Get file content directly from SharePoint sharing URL
        """
        try:
            # First resolve the sharing URL to get the file ID
            file_info = self.resolve_sharepoint_sharing_url(sharing_url)

            if not file_info or not file_info.get('file_id'):
                raise Exception("Could not resolve SharePoint sharing URL to file ID")

            file_id = file_info['file_id']

            # Now download using the file ID
            return self.download_file_content(file_id)

        except Exception as e:
            print(f"❌ Error getting content from sharing URL: {str(e)}")
            raise e

    def get_resized_image(self, file_id, max_width=800, max_height=600, quality=85):
        """Download and resize image from OneDrive"""
        try:
            # Download original image
            image_content = self.download_file_content(file_id)

            # Open with PIL
            image = Image.open(io.BytesIO(image_content))

            # Convert to RGB if necessary (for JPEG compatibility)
            if image.mode in ('RGBA', 'P'):
                image = image.convert('RGB')

            # Calculate new dimensions while maintaining aspect ratio
            original_width, original_height = image.size
            ratio = min(max_width / original_width, max_height / original_height)

            if ratio < 1:  # Only resize if image is larger than max dimensions
                new_width = int(original_width * ratio)
                new_height = int(original_height * ratio)
                image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)

            # Save to bytes
            output = io.BytesIO()
            image.save(output, format='JPEG', quality=quality, optimize=True)
            output.seek(0)

            return output.getvalue()

        except Exception as e:
            print(f"❌ Error processing image: {str(e)}")
            # Return original content if resize fails
            return self.download_file_content(file_id)

    def get_resized_image_from_sharing_url(self, sharing_url, max_width=800, max_height=600, quality=85):
        """
        Get resized image from SharePoint sharing URL
        """
        try:
            # Get the file content
            image_content = self.get_file_content_from_sharing_url(sharing_url)

            # Open with PIL
            image = Image.open(io.BytesIO(image_content))

            # Convert to RGB if necessary (for JPEG compatibility)
            if image.mode in ('RGBA', 'P'):
                image = image.convert('RGB')

            # Calculate new dimensions while maintaining aspect ratio
            original_width, original_height = image.size
            ratio = min(max_width / original_width, max_height / original_height)

            if ratio < 1:  # Only resize if image is larger than max dimensions
                new_width = int(original_width * ratio)
                new_height = int(original_height * ratio)
                image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)

            # Save to bytes
            output = io.BytesIO()
            image.save(output, format='JPEG', quality=quality, optimize=True)
            output.seek(0)

            return output.getvalue()

        except Exception as e:
            print(f"❌ Error processing image from sharing URL: {str(e)}")
            # Fallback to original content
            return self.get_file_content_from_sharing_url(sharing_url)

    def create_folder_if_not_exists(self, folder_path):
        """Create folder structure if it doesn't exist"""
        try:
            access_token = self.get_access_token()

            # Check if folder exists
            check_url = f"https://graph.microsoft.com/v1.0/me/drive/root:/MysteryAudits/{folder_path}"
            headers = {'Authorization': f'Bearer {access_token}'}

            response = requests.get(check_url, headers=headers)

            if response.status_code == 404:
                # Folder doesn't exist, create it
                create_url = "https://graph.microsoft.com/v1.0/me/drive/root:/MysteryAudits:/children"

                data = {
                    "name": folder_path,
                    "folder": {},
                    "@microsoft.graph.conflictBehavior": "rename"
                }

                create_response = requests.post(create_url, headers={
                    **headers,
                    'Content-Type': 'application/json'
                }, json=data)

                if create_response.status_code in [200, 201]:
                    print(f"✅ Folder created: {folder_path}")
                else:
                    print(f"⚠️ Could not create folder {folder_path}: {create_response.text}")

        except Exception as e:
            print(f"⚠️ Error managing folder structure: {str(e)}")
            # Continue without folder creation


# Initialize the uploader
onedrive_uploader = OneDriveUploader()


def upload_to_onedrive_and_get_url(image_data, filename, use_date_folder=True):
    """
    Upload image to OneDrive and return the file metadata

    Args:
        image_data: Base64 encoded image or binary data
        filename: Name for the file
        use_date_folder: Whether to organize files in date folders

    Returns:
        dict: File metadata including file_id
    """
    try:
        print(f"📤 Starting OneDrive upload for {filename}")

        # Create folder structure based on date if requested
        folder_path = ""
        if use_date_folder:
            today = datetime.now()
            folder_path = f"{today.year}/{today.month:02d}-{today.strftime('%B')}"
            onedrive_uploader.create_folder_if_not_exists(folder_path)

        # Upload the file
        file_metadata = onedrive_uploader.upload_file(image_data, filename, folder_path)

        print(f"✅ OneDrive upload completed successfully")
        return file_metadata

    except Exception as e:
        print(f"❌ OneDrive upload failed: {str(e)}")
        raise Exception(f"Failed to upload to OneDrive: {str(e)}")


def get_onedrive_file_info(file_id_or_url):
    """Get information about a file in OneDrive - handles both IDs and SharePoint URLs"""
    try:
        if isinstance(file_id_or_url, str) and 'sharepoint.com' in file_id_or_url:
            # Handle SharePoint sharing URL
            return onedrive_uploader.resolve_sharepoint_sharing_url(file_id_or_url)
        else:
            # Handle regular file ID
            access_token = onedrive_uploader.get_access_token()
            url = f"https://graph.microsoft.com/v1.0/me/drive/items/{file_id_or_url}"
            headers = {'Authorization': f'Bearer {access_token}'}
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()

    except Exception as e:
        print(f"❌ Error getting OneDrive file info: {str(e)}")
        return None


def get_onedrive_image_content(file_id_or_url, resize=True):
    """
    Enhanced function that handles both file IDs and SharePoint sharing URLs
    """
    try:
        # Check if it's a SharePoint sharing URL
        if isinstance(file_id_or_url, str) and 'sharepoint.com' in file_id_or_url:
            print(f"🔗 Detected SharePoint sharing URL: {file_id_or_url}")

            if resize:
                return onedrive_uploader.get_resized_image_from_sharing_url(file_id_or_url)
            else:
                return onedrive_uploader.get_file_content_from_sharing_url(file_id_or_url)
        else:
            # Handle as regular file ID
            print(f"🆔 Treating as file ID: {file_id_or_url}")

            if resize:
                return onedrive_uploader.get_resized_image(file_id_or_url)
            else:
                return onedrive_uploader.download_file_content(file_id_or_url)

    except Exception as e:
        print(f"❌ Error getting image content: {str(e)}")
        return None


def delete_onedrive_file(file_id):
    """Delete a file from OneDrive"""
    try:
        access_token = onedrive_uploader.get_access_token()

        url = f"https://graph.microsoft.com/v1.0/me/drive/items/{file_id}"
        headers = {'Authorization': f'Bearer {access_token}'}

        response = requests.delete(url, headers=headers)
        response.raise_for_status()

        print(f"✅ File deleted from OneDrive: {file_id}")
        return True

    except Exception as e:
        print(f"❌ Error deleting OneDrive file: {str(e)}")
        return False


def convert_sharepoint_urls_to_file_ids(mongo_db):
    """
    One-time migration function to convert SharePoint URLs to file IDs in database
    """
    try:
        converted_count = 0
        failed_count = 0

        # Find all audits with SharePoint URLs
        sharepoint_query = {
            "$or": [
                {"start_image_file_id": {"$regex": "sharepoint.com"}},
                {"end_image_file_id": {"$regex": "sharepoint.com"}},
                {"audit_sheet_image_file_id": {"$regex": "sharepoint.com"}}
            ]
        }

        audits_with_sharepoint_urls = list(mongo_db.school_audits.find(sharepoint_query))

        print(f"🔍 Found {len(audits_with_sharepoint_urls)} audits with SharePoint URLs")

        for audit in audits_with_sharepoint_urls:
            update_fields = {}

            # Process each image field
            for field in ['start_image_file_id', 'end_image_file_id', 'audit_sheet_image_file_id']:
                if field in audit:
                    value = audit[field]

                    if isinstance(value, str) and 'sharepoint.com' in value:
                        print(f"🔄 Converting {field} for audit {audit['_id']}")

                        # Resolve SharePoint URL to file ID
                        file_info = onedrive_uploader.resolve_sharepoint_sharing_url(value)

                        if file_info and file_info.get('file_id'):
                            update_fields[field] = file_info['file_id']
                            # Keep original URL as backup
                            update_fields[f"{field}_original_sharepoint_url"] = value
                            converted_count += 1
                            print(f"✅ Converted {field}: {file_info['file_id']}")
                        else:
                            print(f"❌ Failed to convert {field}: {value}")
                            failed_count += 1

            # Update the audit record if we have conversions
            if update_fields:
                mongo_db.school_audits.update_one(
                    {"_id": audit['_id']},
                    {"$set": update_fields}
                )

        return {
            'converted_count': converted_count,
            'failed_count': failed_count,
            'total_audits_processed': len(audits_with_sharepoint_urls)
        }

    except Exception as e:
        print(f"❌ Error converting SharePoint URLs: {str(e)}")
        return {'error': str(e)}