# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import json
from abc import abstractmethod
from typing import Any, Dict, Generic, Iterator, List, Optional, TypeVar, Union

from aws_lambda_powertools import Logger
from langchain_core.embeddings import Embeddings
from pydantic import BaseModel, ConfigDict

INPUT_TYPE = TypeVar("INPUT_TYPE", bound=Union[str, List[str]])
OUTPUT_TYPE = TypeVar("OUTPUT_TYPE", bound=Union[str, List[List[float]], Iterator])

logger = Logger()


class ContentHandlerBase(Generic[INPUT_TYPE, OUTPUT_TYPE]):
    """Handler class to transform input from LLM to a
    format that SageMaker endpoint expects.

    Similarly, the class handles transforming output from the
    SageMaker endpoint to a format that LLM class expects.
    """

    """
    Example:
        .. code-block:: python

            class ContentHandler(ContentHandlerBase):
                content_type = "application/json"
                accepts = "application/json"

                def transform_input(self, prompt: str, model_kwargs: Dict) -> bytes:
                    input_str = json.dumps({prompt: prompt, **model_kwargs})
                    return input_str.encode('utf-8')

                def transform_output(self, output: bytes) -> str:
                    response_json = json.loads(output.read().decode("utf-8"))
                    return response_json[0]["generated_text"]
    """

    content_type: Optional[str] = "text/plain"
    """The MIME type of the input data passed to endpoint"""

    accepts: Optional[str] = "text/plain"
    """The MIME type of the response data returned from endpoint"""

    @abstractmethod
    def transform_input(self, prompt: INPUT_TYPE, model_kwargs: Dict) -> bytes:
        """Transforms the input to a format that model can accept
        as the request Body. Should return bytes or seekable file
        like object in the format specified in the content_type
        request header.
        """

    @abstractmethod
    def transform_output(self, output: bytes) -> OUTPUT_TYPE:
        """Transforms the output from the model to string that
        the LLM class expects.
        """


class EmbeddingsContentHandler(ContentHandlerBase[List[str], List[List[float]]]):
    pass


class SageMakerEmbeddingsContentHandler(EmbeddingsContentHandler):
    content_type = "application/json"
    accepts = "application/json"

    def transform_input(self, prompts: List[str], model_kwargs: Dict) -> bytes:
        input_str = json.dumps({"type": "embeddings", "input": prompts, **model_kwargs})
        return input_str.encode()

    def transform_output(self, output: bytes) -> List[List[float]]:
        response_json = json.loads(output.read().decode())  # type: ignore
        return response_json  # type: ignore


class SagemakerEndpointEmbeddings(BaseModel, Embeddings):
    """Custom Sagemaker Inference Endpoints.

    To use, you must supply the endpoint name from your deployed
    Sagemaker model & the region where it is deployed.

    To authenticate, the AWS client uses the following methods to
    automatically load credentials:
    https://boto3.amazonaws.com/v1/documentation/api/latest/guide/credentials.html

    If a specific credential profile should be used, you must pass
    the name of the profile from the ~/.aws/credentials file that is to be used.

    Make sure the credentials / roles used have the required policies to
    access the Sagemaker endpoint.
    See: https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies.html
    """
    client: Any = None

    endpoint_name: str = ""
    """The name of the endpoint from the deployed Sagemaker model.
    Must be unique within an AWS Region."""

    region_name: str = ""
    """The aws region where the Sagemaker model is deployed, eg. `us-west-2`."""

    credentials_profile_name: Optional[str] = None
    """The name of the profile in the ~/.aws/credentials or ~/.aws/config files, which
    has either access keys or role information specified.
    If not specified, the default credential profile or, if on an EC2 instance,
    credentials from IMDS will be used.
    See: https://boto3.amazonaws.com/v1/documentation/api/latest/guide/credentials.html
    """

    content_handler: EmbeddingsContentHandler = SageMakerEmbeddingsContentHandler()
    """The content handler class that provides an input and
    output transform functions to handle formats between LLM
    and the endpoint.
    """

    model_kwargs: Optional[Dict] = None
    """Keyword arguments to pass to the model."""

    endpoint_kwargs: Optional[Dict] = None
    """Optional attributes passed to the invoke_endpoint
    function. See `boto3`_. docs for more info.
    .. _boto3: <https://boto3.amazonaws.com/v1/documentation/api/latest/index.html>
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True, extra="forbid", protected_namespaces=()
    )

    def _embedding_func(self, texts: List[str]) -> List[List[float]]:
        """Call out to SageMaker Inference embedding endpoint."""
        # replace newlines, which can negatively affect performance.
        texts = [x.replace("\n", " ") for x in texts]
        _model_kwargs = self.model_kwargs or {}
        _endpoint_kwargs = self.endpoint_kwargs or {}

        body = self.content_handler.transform_input(texts, _model_kwargs)
        content_type = self.content_handler.content_type
        accepts = self.content_handler.accepts

        # send request
        try:
            response = self.client.invoke_endpoint(
                EndpointName=self.endpoint_name,
                Body=body,
                ContentType=content_type,
                Accept=accepts,
                **_endpoint_kwargs,
            )
        except Exception as e:
            raise ValueError(f"Error raised by inference endpoint: {e}")  # noqa: B904

        return self.content_handler.transform_output(response["Body"])

    def embed_documents(self, texts: List[str], chunk_size: int = 64) -> List[List[float]]:
        """Compute doc embeddings using a SageMaker Inference Endpoint.

        Args:
        ----
            texts: The list of texts to embed.
            chunk_size: The chunk size defines how many input texts will
                be grouped together as request. If None, will use the
                chunk size specified by the class.


        Returns:
        -------
            List of embeddings, one for each text.
        """
        results = []
        _chunk_size = len(texts) if chunk_size > len(texts) else chunk_size
        for i in range(0, len(texts), _chunk_size):
            response = self._embedding_func(texts[i : i + _chunk_size])
            results.extend(response)
        return results

    def embed_query(self, text: str) -> List[float]:
        """Compute query embeddings using a SageMaker inference endpoint.

        Args:
        ----
            text: The text to embed.

        Returns:
        -------
            Embeddings for the text.
        """
        return self._embedding_func([text])[0]
