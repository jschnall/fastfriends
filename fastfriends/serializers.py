from rest_framework import serializers


class ExtensibleModelSerializerOptions(serializers.SerializerOptions):
    """
    Meta class options for ExtensibleModelSerializerOptions
    """
    def __init__(self, meta):
        super(ExtensibleModelSerializerOptions, self).__init__(meta)
        self.model = getattr(meta, 'model', None)
        self.read_only_fields = getattr(meta, 'read_only_fields', ())
        self.write_only_fields = getattr(meta, 'write_only_fields', ())
        self.non_native_fields = getattr(meta, 'non_native_fields', ())


class ExtensibleModelSerializer(serializers.ModelSerializer):
    """
    ModelSerializer in which non native extra fields can be specified.
    """
    
    _options_class = ExtensibleModelSerializerOptions
    
    def restore_object(self, attrs, instance=None):
        """
        Deserialize a dictionary of attributes into an object instance.
        You should override this method to control how deserialized objects
        are instantiated.
        """
        
        for field in self.opts.non_native_fields:
            attrs.pop(field)
        
        return super(ExtensibleModelSerializer, self).restore_object(attrs, instance)
    
    def to_native(self, obj):
        """
        Serialize objects -> primitives.
        """
        ret = self._dict_class()
        ret.fields = {}

        for field_name, field in self.fields.items():
            if field_name in self.opts.non_native_fields:
                continue
            field.initialize(parent=self, field_name=field_name)
            key = self.get_field_key(field_name)
            value = field.field_to_native(obj, field_name)
            ret[key] = value
            ret.fields[key] = field
        return ret