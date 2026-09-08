import os
import base64
from datetime import datetime, timedelta
from typing import Optional

from azure.storage.blob.aio import BlobServiceClient
from azure.storage.blob import generate_blob_sas, BlobSasPermissions

from app.config.src import (
    BLOB_CONTAINER_NAME, 
    BLOB_KEYS, 
    BLOB_STORAGE_ACCOUNT_NAME, 
    BLOB_CONTAINER_STRING
)

class AzureBlobManager:
    """
    Manages the lifecycle of audio recording storage in Azure Blob Storage.
    
    This class handles container verification, chunked PCM uploads, and 
    finalization of WAV files by prepending the appropriate headers.
    """

    def __init__(self):
        self.connection_string = BLOB_CONTAINER_STRING
        self.account_name = BLOB_STORAGE_ACCOUNT_NAME
        self.account_key = BLOB_KEYS
        self.container_name = BLOB_CONTAINER_NAME
        
        # If connection string is missing but we have keys, build it dynamically
        if not self.connection_string and self.account_name and self.account_key:
            self.connection_string = (
                f"DefaultEndpointsProtocol=https;AccountName={self.account_name};"
                f"AccountKey={self.account_key};EndpointSuffix=core.windows.net"
            )
        
        if not self.connection_string:
            print("[Blob] Warning: Connection string or credentials missing!")

        self._client = None
        self._container_ensured = False
        self.call_data = {} # call_sid -> {'blocks': [], 'total_bytes': 0}

    async def get_client(self) -> Optional[BlobServiceClient]:
        """Initializes or returns the cached BlobServiceClient."""
        if not self._client and self.connection_string:
            self._client = BlobServiceClient.from_connection_string(self.connection_string)
        return self._client

    async def ensure_container(self) -> bool:
        """Verifies that the target container exists, creating it if necessary."""
        if self._container_ensured:
            return True
        try:
            client = await self.get_client()
            if not client: 
                return False
            container_client = client.get_container_client(self.container_name)
            try:
                await container_client.create_container()
                print(f"[Blob] Created container: {self.container_name}")
            except Exception as e:
                if "ContainerAlreadyExists" not in str(e):
                    print(f"[Blob] Container info: {e}")
            self._container_ensured = True
            return True
        except Exception as e:
            print(f"[Blob] Error verifying container: {e}")
            return False

    async def upload_chunk(self, call_sid: str, audio_data: bytes):
        """
        Uploads a chunk of raw PCM audio data to Azure as a staged block.
        """
        if call_sid not in self.call_data:
            self.call_data[call_sid] = {'blocks': [], 'total_bytes': 0}
        
        # Block IDs must be base64 encoded strings
        index = len(self.call_data[call_sid]['blocks']) + 1
        block_id = base64.b64encode(str(index).zfill(6).encode()).decode()
        
        try:
            await self.ensure_container()
            client = await self.get_client()
            if not client: return

            blob_client = client.get_blob_client(container=self.container_name, blob=f"{call_sid}.wav")
            await blob_client.stage_block(block_id=block_id, data=audio_data)
            self.call_data[call_sid]['blocks'].append(block_id)
            self.call_data[call_sid]['total_bytes'] += len(audio_data)
        except Exception as e:
            print(f"[Blob] Error uploading chunk for {call_sid}: {e}")

    async def finalize_recording(self, call_sid: str, sample_rate: int, num_channels: int) -> Optional[dict]:
        """
        Converts the staged PCM blocks into a playable WAV file.
        1. Generates a 44-byte WAV header.
        2. Stages the header as Block 0.
        3. Commits the block list to finalize the blob.
        """
        if call_sid not in self.call_data or not self.call_data[call_sid]['blocks']:
            return None

        try:
            total_pcm_size = self.call_data[call_sid]['total_bytes']
            header = self._generate_wav_header(total_pcm_size, sample_rate, num_channels)
            
            header_id = base64.b64encode(str(0).zfill(6).encode()).decode()
            client = await self.get_client()
            blob_client = client.get_blob_client(container=self.container_name, blob=f"{call_sid}.wav")
            
            await blob_client.stage_block(block_id=header_id, data=header)

            # Final sequence: [Header] + [PCM Chunk 1] + [PCM Chunk 2] ...
            block_list = [header_id] + self.call_data[call_sid]['blocks']
            await blob_client.commit_block_list(block_list)
            
            recording_info = {
                "call_id": call_sid,
                "storage": "azure_blob",
                "container": self.container_name,
                "blob_name": f"{call_sid}.wav",
                "url": blob_client.url
            }

            del self.call_data[call_sid]
            print(f"[Blob] Successfully finalized recording for {call_sid}")
            return recording_info
        except Exception as e:
            print(f"[Blob] Error finalizing recording for {call_sid}: {e}")
            return None

    def get_sas_url(self, call_sid: str) -> Optional[str]:
        """Generates a temporary (2 hour) secure access URL for the recording."""
        blob_name = f"{call_sid}.wav"
        try:
            sas_token = generate_blob_sas(
                account_name=self.account_name,
                account_key=self.account_key,
                container_name=self.container_name,
                blob_name=blob_name,
                permission=BlobSasPermissions(read=True),
                start=datetime.utcnow() - timedelta(minutes=15),
                expiry=datetime.utcnow() + timedelta(hours=2)
            )
            return f"https://{self.account_name}.blob.core.windows.net/{self.container_name}/{blob_name}?{sas_token}"
        except Exception as e:
            print(f"[Blob] Error generating SAS URL for {call_sid}: {e}")
            return None

    def _generate_wav_header(self, pcm_size: int, sample_rate: int, num_channels: int) -> bytes:
        """Constructs a 44-byte RIFF/WAV header for raw PCM data."""
        header = bytearray()
        header.extend(b'RIFF')
        header.extend((36 + pcm_size).to_bytes(4, 'little'))
        header.extend(b'WAVE')
        header.extend(b'fmt ')
        header.extend((16).to_bytes(4, 'little')) 
        header.extend((1).to_bytes(2, 'little'))  
        header.extend(num_channels.to_bytes(2, 'little'))
        header.extend(sample_rate.to_bytes(4, 'little'))
        header.extend((sample_rate * num_channels * 2).to_bytes(4, 'little')) 
        header.extend((num_channels * 2).to_bytes(2, 'little')) 
        header.extend((16).to_bytes(2, 'little')) 
        header.extend(b'data')
        header.extend(pcm_size.to_bytes(4, 'little'))
        return bytes(header)