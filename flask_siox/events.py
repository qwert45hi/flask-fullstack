from __future__ import annotations

from collections import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Type, Sequence

from flask_socketio import emit
from pydantic import BaseModel

from .utils import remove_none, unpack_params, render_model, render_packed


class BaseEvent:  # do not instantiate!
    def __init__(self, name: str = None, namespace: str = None, additional_docs: dict = None):
        self.name = None
        self.namespace = namespace
        self.additional_docs: dict | None = additional_docs
        if name is not None:
            self.attach_name(name)

    def attach_name(self, name: str):
        raise NotImplementedError

    def attach_namespace(self, namespace: str):
        raise NotImplementedError

    def create_doc(self, namespace: str, additional_docs: dict = None):
        raise NotImplementedError


class Event(BaseEvent):  # do not instantiate!
    def __init__(self, model: Type[BaseModel], namespace: str = None, name: str = None,
                 description: str = None, additional_docs: dict = None):
        super().__init__(name, namespace, additional_docs)
        self.model: Type[BaseModel] = model
        self.description: str = description

    def attach_name(self, name: str):
        self.name = name

    def attach_namespace(self, namespace: str):
        self.namespace = namespace

    def create_doc(self, namespace: str = None, additional_docs: dict = None):
        model_name: str = getattr(self.model, "name", None) or self.model.__name__
        if namespace is None:
            namespace = self.namespace
        return remove_none(
            {"description": self.description,
             "tags": [{"name": f"namespace-{namespace}"}] if namespace is None else [],
             "message": {"$ref": f"#/components/messages/{model_name}"}},
            **(self.additional_docs or {}),
            **(additional_docs or {}),
        )


@dataclass()
class ClientEvent(Event):
    def __init__(self, model: Type[BaseModel], ack_model: Type[BaseModel] = None, namespace: str = None,
                 name: str = None, description: str = None, handler: Callable = None,
                 include: set[str] = None, exclude: set[str] = None, force_wrap: bool = None,
                 exclude_none: bool = None, additional_docs: dict = None):
        super().__init__(model, namespace, name, description, additional_docs)
        self._ack_kwargs = {
            "exclude_none": exclude_none is not False,
            "include": include,
            "exclude": exclude,
            "by_alias": True,
        }
        self.handler: Callable[[dict | None], dict] = handler
        self.ack_model: Type[BaseModel] = ack_model
        self.force_wrap: bool = force_wrap is True

    def parse(self, data: dict):
        return self.model.parse_obj(data).dict()

    def _ack_response(self, result) -> dict:
        if isinstance(result, Sequence):
            return render_packed(*unpack_params(self.ack_model, result, **self._ack_kwargs))
        else:
            result = render_model(self.ack_model, result, **self._ack_kwargs)
            return {"data": result} if self.force_wrap else result

    def _handler(self, function: Callable[..., dict]):
        if self.ack_model is None:
            @wraps(function)
            def _handler_inner(data=None):
                return function(**self.parse(data))
        else:
            @wraps(function)
            def _handler_inner(data=None):
                return self._ack_response(function(**self.parse(data)))

        return _handler_inner

    def bind(self, function):
        self.handler = self._handler(function)

    def attach_ack(self, ack_model: Type[BaseModel], include: set[str] = None, exclude: set[str] = None,
                   force_wrap: bool = None, exclude_none: bool = None) -> None:
        self._ack_kwargs = {
            "exclude_none": exclude_none is not False,
            "include": include,
            "exclude": exclude,
            "by_alias": True,
        }
        self.ack_model: Type[BaseModel] = ack_model
        self.force_wrap: bool = force_wrap is True
        if self.handler is not None:
            self.handler = lambda data=None: self._ack_response(self.handler)

    def create_doc(self, namespace: str = None, additional_docs: dict = None):
        return {"publish": super().create_doc(namespace, additional_docs)}


@dataclass()
class ServerEvent(Event):
    def __init__(self, model: Type[BaseModel], namespace: str = None, name: str = None,
                 description: str = None, include: set[str] = None, exclude: set[str] = None,
                 exclude_none: bool = None, additional_docs: dict = None):
        super().__init__(model, namespace, name, description, additional_docs)
        self._emit_kwargs = {
            "exclude_none": exclude_none is not False,
            "include": include,
            "exclude": exclude,
            "by_alias": True,
        }
        self.model.Config.allow_population_by_field_name = True

    def _emit(self, data: dict, namespace: str = None, room: str = None, include_self: bool = True):
        return emit(self.name, data, to=room, include_self=include_self, namespace=namespace)

    def emit(self, _room: str = None, _include_self: bool = True, _data: ... = None, _namespace: str = None, **kwargs):
        if _data is None:
            _data: BaseModel = self.model(**kwargs)
        return self._emit(render_model(self.model, _data, **self._emit_kwargs), _namespace, _room, _include_self)

    def create_doc(self, namespace: str = None, additional_docs: dict = None):
        return {"subscribe": super().create_doc(namespace, additional_docs)}


@dataclass()
class DuplexEvent(BaseEvent):
    def __init__(self, client_event: ClientEvent = None, server_event: ServerEvent = None, use_event: bool = None,
                 namespace: str = None, name: str = None, description: str = None, additional_docs: dict = None):
        super().__init__(name, namespace, additional_docs)
        self.client_event: ClientEvent = client_event
        self.server_event: ServerEvent = server_event
        self.description: str = description
        self.use_event: bool = bool(use_event)

    @classmethod
    def similar(cls, model: Type[BaseModel], ack_model: Type[BaseModel] = None, use_event: bool = None,
                name: str = None, description: str = None, namespace: str = None, handler: Callable = None,
                include: set[str] = None, exclude: set[str] = None, exclude_none: bool = True,
                ack_include: set[str] = None, ack_exclude: set[str] = None, ack_exclude_none: bool = True,
                ack_force_wrap: bool = False, additional_docs: dict = None):
        return cls(ClientEvent(model, ack_model, namespace, name, description, handler,
                               ack_include, ack_exclude, ack_force_wrap, ack_exclude_none),
                   ServerEvent(model, name, namespace, description, include, exclude, exclude_none),
                   use_event, namespace, name, description, additional_docs)

    def attach_name(self, name: str):
        self.name = name
        self.client_event.name = name
        self.server_event.name = name

    def attach_namespace(self, namespace: str):
        self.namespace = namespace
        self.client_event.namespace = namespace
        self.server_event.namespace = namespace

    def emit(self, _room: str = None, _include_self: bool = True, _data: ... = None, _namespace: str = None, **kwargs):
        return self.server_event.emit(_room, _include_self, _data, _namespace, **kwargs)

    def bind(self, function: Callable[..., dict]):
        if self.use_event:
            @wraps(function)
            def duplex_handler(*args, **kwargs):
                return function(*args, event=self, **kwargs)

            return self.client_event.bind(duplex_handler)
        return self.client_event.bind(function)

    def create_doc(self, namespace: str = None, additional_docs: dict = None):
        additional_docs.update(self.additional_docs or {})
        result: dict = self.client_event.create_doc(namespace, additional_docs)
        result.update(self.server_event.create_doc(namespace, additional_docs))
        if self.description is not None:
            result["description"] = self.description
        return result
