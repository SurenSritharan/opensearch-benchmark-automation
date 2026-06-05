import struct
import os

class CohereFvecBulkSource:
    def __init__(self, workload, params, **kwargs):
        # Read parameters passed from workload.json
        self.file_path = params.get("file_path")
        self.bulk_size = params.get("bulk_size", 1000)
        self.index_name = params.get("index")
        
        self.dim = 1024  # Cohere base dimension
        self.vector_size_bytes = 4 + (self.dim * 4)
        
        # Calculate total documents for tracking progress
        self.file_size = os.path.getsize(self.file_path)
        self.total_docs = self.file_size // self.vector_size_bytes

    def partition(self, client_index, total_clients):
        # Automatically allocates a clean file segment per parallel client runner
        return FvecClientPartition(self, client_index, total_clients)

class FvecClientPartition:
    def __init__(self, source, client_index, total_clients):
        self.source = source
        self.bulk_size = source.bulk_size
        self.index_name = source.index_name
        self.vector_size_bytes = source.vector_size_bytes
        self.dim = source.dim
        
        # Segment calculation
        docs_per_client = source.total_docs // total_clients
        self.start_doc = client_index * docs_per_client
        self.end_doc = self.start_doc + docs_per_client if client_index < total_clients - 1 else source.total_docs
        self.current_doc = self.start_doc
        
        # Open an independent file handle for this specific client partition
        self.f = open(source.file_path, "rb")
        self.f.seek(self.current_doc * self.vector_size_bytes)

    @property
    def percent_completed(self):
        total = self.end_doc - self.start_doc
        if total == 0:
            return 1.0
        return (self.current_doc - self.start_doc) / total

    def params(self):
        if self.current_doc >= self.end_doc:
            self.f.close()
            raise StopIteration
        
        docs_to_read = min(self.bulk_size, self.end_doc - self.current_doc)
        tuples = []
        
        for _ in range(docs_to_read):
            # Advance past the 4-byte length indicator
            _ = self.f.read(4)
            # Unpack the 1024 floating point numbers directly from binary bytes
            vec_bytes = self.f.read(self.dim * 4)
            if not vec_bytes:
                break
                
            vec = struct.unpack(f"{self.dim}f", vec_bytes)
            
            # Form standard OpenSearch Bulk API action metadata and document pairings
            action_meta = {"index": {"_index": self.index_name, "_id": str(self.current_doc)}}
            document = {
                "passage_id": str(self.current_doc),
                "text": f"MS MARCO passage verification placeholder text for ID {self.current_doc}",
                "vector": list(vec)
            }
            tuples.append((action_meta, document))
            self.current_doc += 1
            
        if not tuples:
            self.f.close()
            raise StopIteration
            
        return {
            "bulk-size": len(tuples),
            "action-metadata-and-document-tuple-list": tuples
        }

def register(registry):
    # Register the plugin name to map to workload.json
    registry.register_param_source("cohere-fvec-bulk-source", CohereFvecBulkSource)